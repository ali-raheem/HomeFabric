import datetime as dt

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from homefabric.accounts.models import Household, Invitation, Membership, Role, User, hash_token
from homefabric.core import audit
from homefabric.core.concurrency import versioned_update
from homefabric.core.dates import is_valid_timezone
from homefabric.core.errors import DomainError

INVITATION_LIFETIME = dt.timedelta(days=7)


def create_household(*, name: str, owner: User | None, actor=None, timezone_name=None,
                     currency=None) -> Household:
    tz = timezone_name or settings.DEFAULT_HOUSEHOLD_TIMEZONE
    if not is_valid_timezone(tz):
        raise DomainError(f"Unknown timezone: {tz}")
    with transaction.atomic():
        household = Household.objects.create(
            name=name.strip(), timezone=tz,
            currency=(currency or settings.DEFAULT_HOUSEHOLD_CURRENCY).upper(),
        )
        if owner is not None:
            Membership.objects.create(user=owner, household=household, role=Role.OWNER)
        audit.record(actor or owner, "household.create", household, household=household)
    return household


def update_household(household: Household, *, expected_version: int, actor, name: str,
                     timezone_name: str, currency: str) -> None:
    if not is_valid_timezone(timezone_name):
        raise DomainError(f"Unknown timezone: {timezone_name}")
    currency = currency.strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise DomainError("Currency must be a three-letter ISO 4217 code, e.g. GBP.")
    changes = audit.diff(household, {"name": name, "timezone": timezone_name,
                                     "currency": currency}, ["name", "timezone", "currency"])
    with transaction.atomic():
        versioned_update(household, expected_version, name=name.strip(),
                         timezone=timezone_name, currency=currency)
        audit.record(actor, "household.update", household, changes=changes)


def active_owners(household: Household):
    return Membership.objects.filter(
        household=household, role=Role.OWNER, is_active=True, user__is_active=True
    )


def _ensure_not_last_owner(membership: Membership, *, new_role=None, deactivating=False):
    if membership.role != Role.OWNER or not membership.is_active:
        return
    if new_role == Role.OWNER and not deactivating:
        return
    others = active_owners(membership.household).exclude(pk=membership.pk)
    if not others.exists():
        raise DomainError(
            "This is the last active household owner. Appoint another owner before "
            "changing this membership."
        )


def invalidate_sessions(user: User) -> None:
    """Invalidate every session of ``user`` by bumping their session epoch."""
    User.objects.filter(pk=user.pk).update(session_epoch=F("session_epoch") + 1)
    user.refresh_from_db(fields=["session_epoch"])


def change_role(membership: Membership, new_role: str, *, actor) -> None:
    if new_role not in Role.values:
        raise DomainError("Unknown role.")
    with transaction.atomic():
        membership = Membership.objects.select_for_update().get(pk=membership.pk)
        _ensure_not_last_owner(membership, new_role=new_role)
        old = membership.role
        membership.role = new_role
        membership.save(update_fields=["role"])
        audit.record(actor, "membership.role", membership, household=membership.household,
                     changes={"role": [old, new_role]}, label=str(membership.user))


def deactivate_membership(membership: Membership, *, actor) -> None:
    with transaction.atomic():
        membership = Membership.objects.select_for_update().get(pk=membership.pk)
        if not membership.is_active:
            return
        _ensure_not_last_owner(membership, deactivating=True)
        membership.is_active = False
        membership.deactivated_at = timezone.now()
        membership.save(update_fields=["is_active", "deactivated_at"])
        # Unassign their open work so it does not silently stall.
        from homefabric.maintenance.models import Task, TaskStatus

        Task.objects.filter(
            household=membership.household, assignee=membership.user,
            status__in=[TaskStatus.OPEN, TaskStatus.IN_PROGRESS],
        ).update(assignee=None)
        invalidate_sessions(membership.user)
        audit.record(actor, "membership.deactivate", membership, household=membership.household,
                     label=str(membership.user))


def reactivate_membership(membership: Membership, *, actor) -> None:
    membership.is_active = True
    membership.deactivated_at = None
    membership.save(update_fields=["is_active", "deactivated_at"])
    audit.record(actor, "membership.reactivate", membership, household=membership.household,
                 label=str(membership.user))


def deactivate_user(user: User, *, actor) -> None:
    """Instance-wide deactivation (instance administrator only)."""
    with transaction.atomic():
        for m in Membership.objects.filter(user=user, is_active=True, role=Role.OWNER):
            _ensure_not_last_owner(m, deactivating=True)
        if user.is_superuser and not User.objects.filter(
            is_superuser=True, is_active=True
        ).exclude(pk=user.pk).exists():
            raise DomainError("Cannot deactivate the last instance administrator.")
        user.is_active = False
        user.save(update_fields=["is_active"])
        invalidate_sessions(user)
        audit.record(actor, "user.deactivate", user, label=user.username)


def reactivate_user(user: User, *, actor) -> None:
    user.is_active = True
    user.save(update_fields=["is_active"])
    audit.record(actor, "user.reactivate", user, label=user.username)


def build_url(path: str) -> str:
    return settings.PUBLIC_BASE_URL + path


def create_invitation(household: Household, *, role: str, email: str, actor) -> tuple[Invitation, str]:
    if role not in Role.values:
        raise DomainError("Unknown role.")
    token = Invitation.new_token()
    invitation = Invitation.objects.create(
        household=household, role=role, email=email.strip(), token_hash=hash_token(token),
        created_by=actor, expires_at=timezone.now() + INVITATION_LIFETIME,
    )
    url = build_url(reverse("accounts:accept_invitation", args=[token]))
    audit.record(actor, "invitation.create", invitation, household=household,
                 label=email or f"{role} link")
    return invitation, url


def send_invitation_email(invitation: Invitation, url: str) -> bool:
    if not (settings.EMAIL_CONFIGURED and invitation.email):
        return False
    send_mail(
        subject=f"You have been invited to {invitation.household.name} on HomeFabric",
        message=(
            f"You have been invited to join the household '{invitation.household.name}' "
            f"as {invitation.get_role_display()}.\n\nAccept the invitation (expires "
            f"{invitation.expires_at:%d %b %Y %H:%M} UTC):\n{url}\n\n"
            "The link works once. Ignore this email if you did not expect it."
        ),
        from_email=None, recipient_list=[invitation.email], fail_silently=False,
    )
    return True


def find_invitation(token: str) -> Invitation | None:
    return Invitation.objects.select_related("household").filter(
        token_hash=hash_token(token)
    ).first()


def accept_invitation(token: str, user: User) -> Membership:
    with transaction.atomic():
        invitation = Invitation.objects.select_for_update().filter(
            token_hash=hash_token(token)
        ).first()
        if invitation is None or not invitation.is_usable:
            raise DomainError("This invitation link is invalid, expired, or already used.")
        # Mark used first; the conditional update guarantees single use.
        claimed = Invitation.objects.filter(pk=invitation.pk, used_at__isnull=True).update(
            used_at=timezone.now(), used_by=user
        )
        if claimed != 1:
            raise DomainError("This invitation link has already been used.")
        membership, created = Membership.objects.get_or_create(
            user=user, household=invitation.household, defaults={"role": invitation.role}
        )
        if not created:
            membership.is_active = True
            membership.deactivated_at = None
            if Role.values.index(invitation.role) > Role.values.index(membership.role):
                membership.role = invitation.role
            membership.save()
        audit.record(user, "invitation.accept", membership, household=invitation.household,
                     label=str(user))
    return membership


def revoke_invitation(invitation: Invitation, *, actor) -> None:
    invitation.revoked_at = timezone.now()
    invitation.save(update_fields=["revoked_at"])
    audit.record(actor, "invitation.revoke", invitation, household=invitation.household)


def password_setup_url(user: User) -> str:
    """Single-use password set/reset link (Django token, invalidated on password change)."""
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return build_url(reverse("accounts:password_reset_confirm", args=[uid, token]))


def cleanup_old_sessions() -> int:
    deleted, _ = Session.objects.filter(expire_date__lt=timezone.now()).delete()
    return deleted


def assignable_users(household: Household):
    """Active users with an active editor/owner membership in ``household``."""
    return User.objects.filter(
        is_active=True, memberships__household=household, memberships__is_active=True,
        memberships__role__in=[Role.EDITOR, Role.OWNER],
    ).distinct().order_by("display_name", "username")


def active_member_users(household: Household, roles=None):
    qs = User.objects.filter(
        is_active=True, memberships__household=household, memberships__is_active=True
    )
    if roles:
        qs = qs.filter(memberships__role__in=roles)
    return qs.distinct()
