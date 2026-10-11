"""Archiving, restoring and permanently deleting whole households.

These are instance-administrator operations. Deletion is only allowed for an
archived household, needs the exact household name as confirmation, and runs
in one transaction. Stored files are removed only after the database commit
succeeds, so a failed deletion never loses documents.
"""

from __future__ import annotations

import contextlib
import shutil
import uuid

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from homefabric.accounts.models import Household, Membership, User
from homefabric.core import audit
from homefabric.core.errors import DomainError


def summary(household: Household) -> dict:
    """What a household holds, for confirmation screens and the audit log."""
    from homefabric.assets.models import Entity
    from homefabric.documents.models import Document, StoredBlob
    from homefabric.maintenance.models import ActivityEntry, MaintenanceSchedule, Task

    return {
        "members": Membership.objects.filter(household=household).count(),
        "items": Entity.objects.filter(household=household).count(),
        "documents": Document.objects.filter(household=household).count(),
        "file_bytes": StoredBlob.objects.filter(household=household).aggregate(
            n=Sum("size"))["n"] or 0,
        "history": ActivityEntry.objects.filter(household=household).count(),
        "tasks": Task.objects.filter(household=household).count(),
        "schedules": MaintenanceSchedule.objects.filter(household=household).count(),
    }


def sole_members(household: Household):
    """Active users whose only household is this one (excluding administrators)."""
    other = Membership.objects.exclude(household=household).values("user_id")
    return User.objects.filter(memberships__household=household, is_active=True,
                               is_superuser=False).exclude(pk__in=other).distinct()


def archive_household(household: Household, *, actor, reason: str = "") -> None:
    from homefabric.notifications.models import DeliveryState, NotificationDelivery

    if household.is_archived:
        return
    with transaction.atomic():
        Household.objects.filter(pk=household.pk).update(
            archived_at=timezone.now(), archived_by=actor, archive_reason=reason[:300])
        NotificationDelivery.objects.filter(
            household=household, state=DeliveryState.PENDING
        ).update(state=DeliveryState.SUPPRESSED, last_error="Household archived")
        audit.record(actor, "household.archive", household, household=household,
                     changes={"reason": reason})
    household.refresh_from_db()


def restore_household(household: Household, *, actor) -> None:
    if not household.is_archived:
        return
    Household.objects.filter(pk=household.pk).update(archived_at=None, archived_by=None,
                                                     archive_reason="")
    audit.record(actor, "household.restore", household, household=household)
    household.refresh_from_db()


def delete_household(household: Household, *, actor, confirm_name: str,
                     deactivate_sole_members: bool = False) -> dict:
    """Permanently remove a household and everything it owns."""
    from homefabric.assets.models import Entity, EntityRelationship, EntityType, Tag
    from homefabric.documents import storage
    from homefabric.documents.models import (
        ActivityDocument,
        Document,
        DocumentRevision,
        EntityDocument,
        StoredBlob,
        TaskDocument,
    )
    from homefabric.maintenance.models import (
        ActivityEntry,
        MaintenanceSchedule,
        ReminderRule,
        Task,
    )
    from homefabric.notifications.models import NotificationDelivery

    if not household.is_archived:
        raise DomainError("Archive the household before deleting it permanently.")
    if confirm_name.strip() != household.name:
        raise DomainError("Type the household's exact name to confirm deletion.")
    counts = summary(household)
    name, hid = household.name, household.pk
    deactivated = []
    with transaction.atomic():
        if deactivate_sole_members:
            for user in sole_members(household):
                user.is_active = False
                user.save(update_fields=["is_active"])
                from homefabric.accounts.services import invalidate_sessions

                invalidate_sessions(user)
                deactivated.append(user.username)
        hh = {"household": household}
        NotificationDelivery.objects.filter(**hh).delete()
        ActivityDocument.objects.filter(**hh).delete()
        TaskDocument.objects.filter(**hh).delete()
        EntityDocument.objects.filter(**hh).delete()
        ReminderRule.objects.filter(**hh).delete()
        Task.objects.filter(**hh).update(related_task=None, source_activity=None)
        Task.objects.filter(**hh).delete()
        MaintenanceSchedule.objects.filter(**hh).delete()
        ActivityEntry.objects.filter(**hh).update(correction_of=None)
        ActivityEntry.objects.filter(**hh).delete()
        Document.objects.filter(**hh).update(current_revision=None)
        DocumentRevision.objects.filter(document__household=household).delete()
        Document.objects.filter(**hh).delete()
        storage_keys = list(StoredBlob.objects.filter(**hh).values_list("storage_key", flat=True))
        StoredBlob.objects.filter(**hh).delete()
        EntityRelationship.objects.filter(**hh).delete()
        Entity.objects.filter(**hh).delete()
        EntityType.objects.filter(**hh).delete()
        Tag.objects.filter(**hh).delete()
        # Anything missed is still PROTECTed and makes this delete fail, rolling
        # the whole transaction back rather than leaving a half-deleted household.
        household.delete()
        audit.record(actor, "household.delete", None, label=name,
                     changes={"household_id": str(hid), **counts,
                              "deactivated_users": deactivated})

        def remove_files():
            for key in storage_keys:
                with contextlib.suppress(OSError, ValueError):
                    storage.path_for(key).unlink(missing_ok=True)
            folder = storage.blob_root() / uuid.UUID(str(hid)).hex
            shutil.rmtree(folder, ignore_errors=True)

        transaction.on_commit(remove_files)
    return {"name": name, **counts, "deactivated_users": deactivated}
