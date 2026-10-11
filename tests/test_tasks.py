"""Tasks and service history: A04, A05, A10 (history corrections), assignment rules."""

import datetime as dt

import pytest
from django.urls import reverse

from homefabric.accounts.services import deactivate_membership
from homefabric.core.errors import ConcurrencyError, DomainError
from homefabric.documents.models import ActivityDocument
from homefabric.maintenance import services
from homefabric.maintenance.models import ActivityEntry, EventType, Task, TaskKind, TaskStatus
from tests.conftest import login, pdf

pytestmark = pytest.mark.django_db


@pytest.fixture
def service_task(home, owner):
    schedule = services.create_schedule(
        home["boiler"], actor=owner, title="Annual boiler service", mode="fixed",
        interval_count=1, interval_unit="year", anchor_date=dt.date.today() + dt.timedelta(days=20))
    return Task.objects.get(schedule=schedule)


def test_a04_completing_booking_leaves_service_open(home, owner, service_task):
    booking = services.create_task(home["boiler"], actor=owner, title="Book engineer",
                                   kind=TaskKind.BOOKING, related_task=service_task,
                                   due_date=dt.date.today())
    entry = services.complete_task(booking, actor=owner, expected_version=booking.version,
                                   completed_on=dt.date.today(), event_type=EventType.SERVICE)
    service_task.refresh_from_db()
    assert service_task.status == TaskStatus.OPEN
    assert entry.event_type == EventType.BOOKING  # never a fabricated service record
    assert not ActivityEntry.objects.filter(entity=home["boiler"],
                                            event_type=EventType.SERVICE).exists()


def test_a05_complete_service_with_evidence_and_next_due(home, owner, service_task):
    today = dt.date.today()
    entry = services.complete_task(
        service_task, actor=owner, expected_version=service_task.version, completed_on=today,
        event_type=EventType.SERVICE, notes="Flue check OK", provider="Gas Safe Ltd",
        amount="120.50", currency="GBP", uploads=[pdf("invoice.pdf")])
    service_task.refresh_from_db()
    assert service_task.status == TaskStatus.COMPLETED and service_task.completed_on == today
    assert entry.amount_minor == 12050 and entry.currency == "GBP"
    assert entry.provider == "Gas Safe Ltd" and entry.task == service_task
    link = ActivityDocument.objects.get(activity=entry)
    assert link.revision.original_filename == "invoice.pdf"
    # Fixed calendar: next occurrence stays on the anchor anniversary.
    services.generate_for_schedule(service_task.schedule,
                                   today=service_task.due_date + dt.timedelta(days=300))
    assert Task.objects.filter(schedule=service_task.schedule,
                               due_date=service_task.due_date.replace(
                                   year=service_task.due_date.year + 1)).exists()


def test_skip_creates_history_but_no_service(home, owner, service_task):
    services.skip_task(service_task, actor=owner, expected_version=service_task.version,
                       reason="Boiler replaced")
    service_task.refresh_from_db()
    assert service_task.status == TaskStatus.SKIPPED
    assert ActivityEntry.objects.filter(task=service_task,
                                        event_type=EventType.TASK_SKIPPED).count() == 1
    assert not ActivityEntry.objects.filter(event_type=EventType.SERVICE).exists()
    with pytest.raises(DomainError):
        services.skip_task(service_task, actor=owner, expected_version=service_task.version,
                           reason="again")


def test_skip_requires_reason(home, owner):
    task = services.create_task(home["boiler"], actor=owner, title="X")
    with pytest.raises(DomainError):
        services.skip_task(task, actor=owner, expected_version=task.version, reason="  ")


def test_completed_task_cannot_be_reopened_follow_up_instead(home, owner):
    task = services.create_task(home["boiler"], actor=owner, title="Fix")
    services.complete_task(task, actor=owner, expected_version=task.version,
                           completed_on=dt.date.today())
    task.refresh_from_db()
    with pytest.raises(DomainError):
        services.update_task(task, actor=owner, expected_version=task.version, title="Y",
                             description="", due_date=None, assignee=None)
    follow = services.create_follow_up(task, actor=owner, title="Fix properly")
    assert follow.related_task == task


def test_inactive_users_cannot_receive_assignments(home, owner, editor, viewer, household):
    with pytest.raises(DomainError):
        services.create_task(home["boiler"], actor=owner, title="X", assignee=viewer)
    task = services.create_task(home["boiler"], actor=owner, title="X", assignee=editor)
    from homefabric.accounts.models import Membership

    deactivate_membership(Membership.objects.get(user=editor, household=household), actor=owner)
    task.refresh_from_db()
    assert task.assignee is None  # open work unassigned, not silently stalled
    with pytest.raises(DomainError):
        services.create_task(home["boiler"], actor=owner, title="Y", assignee=editor)


def test_a10_history_correction_preserves_original(home, owner, editor):
    original = services.add_activity(home["boiler"], actor=owner, event_type=EventType.SERVICE,
                                     occurred_on=dt.date(2025, 5, 1), title="Service",
                                     amount="90", currency="GBP", provider="Acme")
    corrected = services.correct_activity(original, actor=editor, reason="Invoice said £95",
                                          occurred_on=dt.date(2025, 5, 1), title="Service",
                                          notes="", provider="Acme", amount="95", currency="GBP")
    original.refresh_from_db()
    assert original.superseded and original.amount_minor == 9000 and original.author == owner
    assert corrected.correction_of == original and corrected.author == editor
    assert services.correction_chain(corrected) == [original, corrected]
    with pytest.raises(DomainError):
        services.correct_activity(original, actor=editor, reason="again", occurred_on=dt.date(
            2025, 5, 1), title="", notes="", provider="", amount=None, currency="")


def test_money_never_float_and_currencies_not_mixed():
    from homefabric.core.money import format_money, to_minor, totals_by_currency

    assert to_minor("0.1", "GBP") + to_minor("0.2", "GBP") == 30
    assert to_minor("1000", "JPY") == 1000
    assert totals_by_currency([(100, "GBP"), (250, "EUR"), (50, "GBP")]) == {"GBP": 150,
                                                                          "EUR": 250}
    assert format_money(12050, "GBP") == "£120.50"


def test_future_completion_rejected(home, owner):
    task = services.create_task(home["boiler"], actor=owner, title="X")
    with pytest.raises(DomainError):
        services.complete_task(task, actor=owner, expected_version=task.version,
                               completed_on=dt.date.today() + dt.timedelta(days=10))


def test_task_concurrent_edit_conflict(home, owner, editor):
    task = services.create_task(home["boiler"], actor=owner, title="X")
    v = task.version
    services.update_task(task, actor=owner, expected_version=v, title="A", description="",
                         due_date=None, assignee=None)
    with pytest.raises(ConcurrencyError):
        services.update_task(Task.objects.get(pk=task.pk), actor=editor, expected_version=v,
                             title="B", description="", due_date=None, assignee=None)


def test_web_complete_flow_with_upload(home, owner, client, service_task):
    login(client, owner)
    url = reverse("maintenance:task_complete", args=[service_task.pk])
    r = client.post(url, {"completed_on": dt.date.today().isoformat(), "event_type": "service",
                          "provider": "Acme", "notes": "done", "amount": "80", "currency": "GBP",
                          "version": service_task.version, "evidence": [pdf("inv.pdf")]})
    assert r.status_code == 302, r.content.decode()[:2000]
    service_task.refresh_from_db()
    assert service_task.status == TaskStatus.COMPLETED


def test_observation_to_repair_task(home, owner, client):
    """§4.4: add a dated observation and create a repair task from it."""
    login(client, owner)
    r = client.post(reverse("maintenance:activity_create", args=[home["radiator"].pk]), {
        "event_type": "observation", "occurred_on": dt.date.today().isoformat(),
        "title": "Cold at top", "notes": "Needs bleeding", "create_task": "on",
        "currency": "GBP"})
    assert r.status_code == 302 and "from_activity=" in r["Location"]
    page = client.get(r["Location"])
    assert b"Follow up: Cold at top" in page.content
    entry = ActivityEntry.objects.get(title="Cold at top")
    r = client.post(r["Location"], {"entity": home["radiator"].pk, "title": "Bleed radiator",
                                    "kind": "general", "reminders": "on"})
    assert r.status_code == 302
    assert Task.objects.get(title="Bleed radiator").source_activity == entry
