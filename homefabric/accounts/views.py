from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST

from homefabric.accounts import services
from homefabric.accounts.forms import (
    HouseholdCreateForm,
    HouseholdSettingsForm,
    InviteForm,
    LoginForm,
    ProfileForm,
    RoleForm,
    SignupForm,
)
from homefabric.accounts.middleware import SESSION_KEY
from homefabric.accounts.models import Household, Invitation, Membership, User
from homefabric.accounts.permissions import household_view, instance_admin_required
from homefabric.core import audit
from homefabric.core.errors import DomainError
from homefabric.core.urls_util import safe_redirect


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


class PasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "accounts/email/password_reset.txt"
    subject_template_name = "accounts/email/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["email_configured"] = settings.EMAIL_CONFIGURED
        return ctx


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile saved.")
        return redirect("accounts:profile")
    memberships = Membership.objects.filter(user=request.user).select_related("household")
    return render(request, "accounts/profile.html", {"form": form, "memberships": memberships})


@require_POST
@login_required
def sign_out_everywhere(request):
    services.invalidate_sessions(request.user)
    update_session_auth_hash(request, request.user)  # keep this session
    audit.record(request.user, "user.sign_out_everywhere", request.user,
                 label=request.user.username)
    messages.success(request, "All other sessions have been signed out.")
    return redirect("accounts:profile")


@require_POST
@login_required
def switch_household(request):
    household_id = request.POST.get("household")
    allowed = request.user.is_instance_admin or Membership.objects.filter(
        user=request.user, household_id=household_id, is_active=True).exists()
    if allowed and Household.objects.filter(pk=household_id, archived_at__isnull=True).exists():
        request.session[SESSION_KEY] = str(household_id)
    return redirect("core:dashboard")


@login_required
def no_household(request):
    if request.household is not None:
        return redirect("core:dashboard")
    return render(request, "accounts/no_household.html")


def accept_invitation(request, token):
    invitation = services.find_invitation(token)
    if invitation is None or not invitation.is_usable:
        return render(request, "accounts/invitation_invalid.html", status=410)
    if request.user.is_authenticated:
        if request.method == "POST":
            try:
                membership = services.accept_invitation(token, request.user)
            except DomainError as exc:
                messages.error(request, str(exc))
                return redirect("core:dashboard")
            request.session[SESSION_KEY] = str(membership.household_id)
            messages.success(request, f"You joined {membership.household.name}.")
            return redirect("core:dashboard")
        return render(request, "accounts/invitation_accept.html", {"invitation": invitation})
    form = SignupForm(request.POST or None, initial={"email": invitation.email})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            with transaction.atomic():
                user = User.objects.create_user(username=d["username"], email=d.get("email", ""),
                                                password=d["password1"],
                                                display_name=d.get("display_name", ""))
                membership = services.accept_invitation(token, user)
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            login(request, user, backend="homefabric.accounts.backends.ThrottledModelBackend")
            request.session[SESSION_KEY] = str(membership.household_id)
            messages.success(request, f"Welcome! You joined {membership.household.name}.")
            return redirect("core:dashboard")
    return render(request, "accounts/invitation_signup.html",
                  {"invitation": invitation, "form": form})


@household_view("view")
def household_settings(request):
    from homefabric.accounts.permissions import can

    household = request.household
    form = HouseholdSettingsForm(request.POST or None, initial={
        "name": household.name, "timezone": household.timezone,
        "currency": household.currency, "version": household.version})
    if request.method == "POST":
        if not can(request, "manage"):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied
        if form.is_valid():
            try:
                services.update_household(household, expected_version=form.cleaned_data["version"],
                                          actor=request.user, name=form.cleaned_data["name"],
                                          timezone_name=form.cleaned_data["timezone"],
                                          currency=form.cleaned_data["currency"])
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "Household settings saved.")
                return redirect("accounts:household_settings")
    return render(request, "accounts/household_settings.html", {"form": form})


@household_view("view")
def members(request):
    from homefabric.accounts.permissions import can

    household = request.household
    invite_form = InviteForm(request.POST or None)
    new_link = None
    if request.method == "POST":
        if not can(request, "manage"):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied
        if invite_form.is_valid():
            invitation, url = services.create_invitation(
                household, role=invite_form.cleaned_data["role"],
                email=invite_form.cleaned_data.get("email", ""), actor=request.user)
            sent = False
            try:
                sent = services.send_invitation_email(invitation, url)
            except Exception as exc:  # noqa: BLE001
                messages.warning(request, f"Could not send the email ({exc}); share the link "
                                 "below instead.")
            if sent:
                messages.success(request, f"Invitation emailed to {invitation.email}.")
            new_link = url
            invite_form = InviteForm()
    return render(request, "accounts/members.html", {
        "memberships": Membership.objects.filter(household=household).select_related("user")
        .order_by("-is_active", "user__username"),
        "invitations": Invitation.objects.filter(household=household)[:30],
        "invite_form": invite_form, "new_link": new_link, "role_form": RoleForm(),
    })


def _membership(request, pk):
    return get_object_or_404(Membership, pk=pk, household=request.household)


@require_POST
@household_view("manage")
def member_role(request, pk):
    membership = _membership(request, pk)
    form = RoleForm(request.POST)
    if form.is_valid():
        try:
            services.change_role(membership, form.cleaned_data["role"], actor=request.user)
        except DomainError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{membership.user.label} is now "
                             f"{form.cleaned_data['role']}.")
    return redirect("accounts:members")


@require_POST
@household_view("manage")
def member_deactivate(request, pk):
    membership = _membership(request, pk)
    try:
        services.deactivate_membership(membership, actor=request.user)
    except DomainError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{membership.user.label} no longer has access. Their "
                         "history is preserved.")
    return redirect("accounts:members")


@require_POST
@household_view("manage")
def member_reactivate(request, pk):
    membership = _membership(request, pk)
    if not membership.user.is_active:
        messages.error(request, "That account is deactivated instance-wide.")
    else:
        services.reactivate_membership(membership, actor=request.user)
        messages.success(request, f"{membership.user.label} has access again.")
    return redirect("accounts:members")


@require_POST
@household_view("manage")
def invitation_revoke(request, pk):
    invitation = get_object_or_404(Invitation, pk=pk, household=request.household)
    services.revoke_invitation(invitation, actor=request.user)
    messages.success(request, "Invitation revoked.")
    return redirect("accounts:members")


@instance_admin_required
def household_create(request):
    form = HouseholdCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            household = services.create_household(
                name=d["name"], owner=d.get("owner") or request.user, actor=request.user,
                timezone_name=d["timezone"], currency=d["currency"])
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            request.session[SESSION_KEY] = str(household.pk)
            messages.success(request, f"Household '{household.name}' created.")
            return redirect("core:dashboard")
    return render(request, "accounts/household_create.html", {"form": form})


@instance_admin_required
def user_list(request):
    from homefabric.core.pagination import paginate

    users = User.objects.order_by("-is_active", "username").prefetch_related(
        "memberships__household")
    return render(request, "accounts/user_list.html", {"page": paginate(request, users, 50)})


@require_POST
@instance_admin_required
def user_toggle(request, pk):
    user = get_object_or_404(User, pk=pk)
    try:
        if user.is_active:
            services.deactivate_user(user, actor=request.user)
            messages.success(request, f"{user.username} deactivated and signed out.")
        else:
            services.reactivate_user(user, actor=request.user)
            messages.success(request, f"{user.username} reactivated.")
    except DomainError as exc:
        messages.error(request, str(exc))
    return safe_redirect(request, request.POST.get("next"), "accounts:user_list")


@require_POST
@instance_admin_required
def user_setup_link(request, pk):
    user = get_object_or_404(User, pk=pk, is_active=True)
    url = services.password_setup_url(user)
    audit.record(request.user, "user.setup_link", user, label=user.username)
    messages.info(request, f"Single-use password link for {user.username} (24 hours): {url}")
    return redirect("accounts:user_list")


# --- instance administration: household lifecycle -------------------------

class ArchiveHouseholdForm(forms.Form):
    reason = forms.CharField(max_length=300, required=False, label="Reason (optional)")


class DeleteHouseholdForm(forms.Form):
    confirm_name = forms.CharField(label="Type the household's name to confirm")
    deactivate_sole_members = forms.BooleanField(
        required=False, label="Also deactivate accounts that belong only to this household")


@instance_admin_required
def household_list(request):
    from homefabric.accounts import households

    rows = [(h, households.summary(h)) for h in Household.objects.order_by(
        "archived_at", "name")]
    return render(request, "accounts/household_list.html", {"rows": rows})


@instance_admin_required
def household_archive(request, pk):
    from homefabric.accounts import households

    household = get_object_or_404(Household, pk=pk)
    form = ArchiveHouseholdForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        households.archive_household(household, actor=request.user,
                                     reason=form.cleaned_data["reason"])
        messages.success(request, f"'{household.name}' archived. Members no longer see it and "
                         "its reminders have stopped. You can restore it at any time.")
        return redirect("accounts:household_list")
    return render(request, "accounts/household_archive.html", {
        "household": household, "form": form, "summary": households.summary(household)})


@require_POST
@instance_admin_required
def household_restore(request, pk):
    from homefabric.accounts import households

    household = get_object_or_404(Household, pk=pk)
    households.restore_household(household, actor=request.user)
    messages.success(request, f"'{household.name}' restored. Review its schedules: occurrences "
                     "missed while archived are generated as overdue on the next scheduler run.")
    return redirect("accounts:household_list")


@instance_admin_required
def household_export_admin(request, pk):
    import os
    import tempfile

    from django.conf import settings
    from django.http import FileResponse
    from django.utils import timezone
    from django.utils.text import slugify

    from homefabric.operations.export import write_export

    household = get_object_or_404(Household, pk=pk)
    tmp = tempfile.NamedTemporaryFile(prefix="export-", suffix=".zip", dir=settings.STAGING_DIR,
                                      delete=False)
    tmp.close()
    write_export(household, tmp.name)
    audit.record(request.user, "household.export", household, household=household)
    handle = open(tmp.name, "rb")  # noqa: SIM115 - closed by FileResponse
    try:
        os.unlink(tmp.name)
    except OSError:
        pass
    name = f"homefabric-{slugify(household.name) or 'household'}-{timezone.now():%Y%m%d}.zip"
    return FileResponse(handle, as_attachment=True, filename=name,
                        content_type="application/zip")


@instance_admin_required
def household_delete(request, pk):
    from homefabric.accounts import households

    household = get_object_or_404(Household, pk=pk)
    if not household.is_archived:
        messages.error(request, "Archive the household before deleting it permanently.")
        return redirect("accounts:household_list")
    form = DeleteHouseholdForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            result = households.delete_household(
                household, actor=request.user, confirm_name=form.cleaned_data["confirm_name"],
                deactivate_sole_members=form.cleaned_data["deactivate_sole_members"])
        except DomainError as exc:
            form.add_error("confirm_name", str(exc))
        else:
            extra = (f" Deactivated: {', '.join(result['deactivated_users'])}."
                     if result["deactivated_users"] else "")
            messages.success(request, f"'{result['name']}' and all its data were permanently "
                             f"deleted.{extra}")
            return redirect("accounts:household_list")
    return render(request, "accounts/household_delete.html", {
        "household": household, "form": form, "summary": households.summary(household),
        "sole_members": households.sole_members(household)})
