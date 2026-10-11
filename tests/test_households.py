"""Archiving and permanently deleting households."""

import datetime as dt
import io
import json

import pytest
from django.core.management import call_command
from django.urls import reverse

from homefabric.accounts import households, services
from homefabric.accounts.models import Household, Membership, Role, User
from homefabric.assets import services as assets
from homefabric.assets.models import Entity, RelationKind
from homefabric.core.errors import DomainError
from homefabric.core.models import AuditEvent
from homefabric.documents import services as docs
from homefabric.documents import storage
from homefabric.documents.models import StoredBlob
from homefabric.maintenance import services as maint
from homefabric.maintenance.models import ActivityEntry, MaintenanceSchedule, Task
from homefabric.notifications import services as notify
from homefabric.notifications.models import (
    DeliveryState,
    NotificationDelivery,
    NotificationEndpoint,
)
from homefabric.operations.integrity import check
from tests.conftest import PDF_BYTES, login, make_user, pdf

pytestmark = pytest.mark.django_db


@pytest.fixture
def full_house(home, household, owner, editor):
    """A household with every kind of record, including tricky references."""
    assets.add_relationship(home["radiator"], home["heating"], RelationKind.PART_OF, actor=owner)
    et = assets.create_entity_type(household, name="Boiler", base_kind="equipment", actor=owner)
    assets.create_entity(household, actor=owner, data={"name": "Spare", "entity_type": et},
                         tags=["spares"])
    doc = docs.upload_document(household, pdf("m.pdf", PDF_BYTES + b"x"), actor=owner,
                               entity=home["boiler"])
    docs.add_revision(doc, pdf("m2.pdf", PDF_BYTES + b"y"), actor=owner,
                      expected_version=doc.version)
    sched = maint.create_schedule(home["boiler"], actor=owner, title="Service", mode="fixed",
                                  interval_count=1, interval_unit="year",
                                  anchor_date=dt.date.today() + dt.timedelta(days=5))
    service = Task.objects.filter(schedule=sched).first()
    booking = maint.create_task(home["boiler"], actor=owner, title="Book",
                                related_task=service, kind="booking")
    entry = maint.complete_task(booking, actor=owner, expected_version=booking.version,
                                completed_on=dt.date.today(), uploads=[pdf("inv.pdf")])
    maint.correct_activity(entry, actor=owner, reason="typo", occurred_on=dt.date.today(),
                           title="Booked", notes="", provider="", amount=None, currency="")
    obs = maint.add_activity(home["radiator"], actor=owner, event_type="observation",
                             occurred_on=dt.date.today())
    maint.create_task(home["radiator"], actor=owner, title="Bleed", source_activity=obs)
    ep = NotificationEndpoint.objects.create(user=owner, provider="log", display_name="L")
    NotificationDelivery.objects.create(
        household=household, kind="reminder", event_key="k", recipient=owner, endpoint=ep,
        channel="log", task=service, subject="s", body="b", due_at=dt.datetime.now(dt.UTC),
        next_attempt_at=dt.datetime.now(dt.UTC))
    services.create_invitation(household, role=Role.VIEWER, email="", actor=owner)
    return home


def test_archive_hides_household_and_stops_work(full_house, household, owner, client):
    login(client, owner)
    assert client.get(full_house["boiler"].get_absolute_url()).status_code == 200
    households.archive_household(household, actor=None, reason="Moved out")
    assert client.get(reverse("core:dashboard")).status_code == 302  # no household left
    assert NotificationDelivery.objects.get().state == DeliveryState.SUPPRESSED
    assert maint.run_all_schedules()["schedules"] == 0
    assert notify.plan_reminders()["individual"] == 0
    inv = household.invitations.first()
    assert not inv.is_usable
    households.restore_household(household, actor=None)
    assert client.get(full_house["boiler"].get_absolute_url()).status_code == 200


def test_archived_household_hidden_from_switcher(full_house, household, owner, client,
                                                 other_household):
    Membership.objects.create(user=owner, household=other_household, role=Role.VIEWER)
    households.archive_household(household, actor=None)
    login(client, owner)
    client.post(reverse("accounts:switch_household"), {"household": household.pk})
    html = client.get(reverse("core:dashboard")).content.decode()
    assert "Other home" in html and "Test home" not in html


def test_delete_requires_archive_and_exact_name(full_house, household):
    with pytest.raises(DomainError, match="Archive"):
        households.delete_household(household, actor=None, confirm_name="Test home")
    households.archive_household(household, actor=None)
    with pytest.raises(DomainError, match="exact name"):
        households.delete_household(household, actor=None, confirm_name="test home")
    assert Household.objects.filter(pk=household.pk).exists()


def test_delete_removes_everything_and_files(full_house, household, other_household,
                                            other_owner, make_entity, owner, admin_user,
                                            django_capture_on_commit_callbacks):
    keep = make_entity(other_household, "Their boiler", actor=other_owner)
    their_doc = docs.upload_document(other_household, pdf("m.pdf", PDF_BYTES + b"x"),
                                     actor=other_owner)  # same bytes as one of ours
    paths = [storage.path_for(b.storage_key)
             for b in StoredBlob.objects.filter(household=household)]
    assert paths and all(p.exists() for p in paths)
    hid = household.pk
    households.archive_household(household, actor=admin_user)
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        result = households.delete_household(household, actor=admin_user,
                                             confirm_name="Test home")
    assert all(p.exists() for p in paths)  # files survive until the commit succeeds
    for callback in callbacks:
        callback()
    assert result["items"] == 8 and result["documents"] == 2
    assert not Household.objects.filter(pk=hid).exists()
    for model in (Entity, Task, MaintenanceSchedule, ActivityEntry, StoredBlob):
        assert not model.objects.filter(household_id=hid).exists()
    assert not any(p.exists() for p in paths)
    # The other household, including identical file content, is untouched.
    assert Entity.objects.filter(pk=keep.pk).exists()
    assert storage.path_for(their_doc.current_revision.blob.storage_key).exists()
    assert User.objects.filter(username="owner").exists()  # accounts are kept
    ev = AuditEvent.objects.get(action="household.delete")
    assert ev.object_label == "Test home" and ev.changes["household_id"] == str(hid)
    assert check()["ok"]


def test_delete_can_deactivate_sole_members(full_house, household, owner, editor,
                                            other_household):
    Membership.objects.create(user=editor, household=other_household, role=Role.VIEWER)
    households.archive_household(household, actor=None)
    assert list(households.sole_members(household)) == [owner]
    result = households.delete_household(household, actor=None, confirm_name="Test home",
                                         deactivate_sole_members=True)
    assert result["deactivated_users"] == ["owner"]
    assert not User.objects.get(username="owner").is_active
    assert User.objects.get(username="editor").is_active


def test_failed_delete_rolls_back_and_keeps_files(full_house, household, monkeypatch):
    households.archive_household(household, actor=None)
    paths = [storage.path_for(b.storage_key)
             for b in StoredBlob.objects.filter(household=household)]

    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(Household, "delete", boom)
    with pytest.raises(RuntimeError):
        households.delete_household(household, actor=None, confirm_name="Test home")
    assert Entity.objects.filter(household=household).count() == 8
    assert all(p.exists() for p in paths)


def test_admin_web_flow(full_house, household, admin_user, client):
    login(client, admin_user)
    page = client.get(reverse("accounts:household_list")).content.decode()
    assert "Test home" in page and "Archive" in page
    r = client.get(reverse("accounts:household_delete", args=[household.pk]))
    assert r.status_code == 302  # must archive first
    client.post(reverse("accounts:household_archive", args=[household.pk]), {"reason": "Sold"})
    household.refresh_from_db()
    assert household.is_archived and household.archive_reason == "Sold"
    export = client.get(reverse("accounts:household_export_admin", args=[household.pk]))
    assert export.status_code == 200
    r = client.post(reverse("accounts:household_delete", args=[household.pk]),
                    {"confirm_name": "wrong"})
    assert r.status_code == 200 and Household.objects.filter(pk=household.pk).exists()
    r = client.post(reverse("accounts:household_delete", args=[household.pk]),
                    {"confirm_name": "Test home"})
    assert r.status_code == 302 and not Household.objects.filter(pk=household.pk).exists()


def test_owners_cannot_archive_or_delete(household, owner, client):
    login(client, owner)
    for name in ("household_list", "household_archive", "household_delete"):
        args = [] if name == "household_list" else [household.pk]
        assert client.get(reverse(f"accounts:{name}", args=args)).status_code == 403
    assert client.post(reverse("accounts:household_restore", args=[household.pk])).status_code \
        == 403


def test_cli(full_house, household):
    def run(*args):
        out = io.StringIO()
        call_command("household", "--json", *args, stdout=out)
        return json.loads(out.getvalue())

    assert run("list")["households"][0]["items"] == 8
    with pytest.raises(SystemExit):
        run("delete", "Test home", "--confirm-name", "Test home")  # not archived
    run("archive", "Test home", "--reason", "sold")
    assert run("delete", str(household.pk), "--confirm-name", "Test home")["ok"]
    assert not Household.objects.exists()


def test_backup_restore_unaffected_by_archived_household(full_house, household):
    households.archive_household(household, actor=None)
    from homefabric.operations.export import build_export

    assert build_export(household)["entities"]  # still exportable while archived


def test_unused_helper(db):
    assert make_user("z").is_active
