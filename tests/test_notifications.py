"""Notifications: A07, A08, reminder semantics and endpoint handling (spec §8)."""

import datetime as dt
import threading

import pytest
from django.test import override_settings
from django.utils import timezone

from homefabric.accounts.models import Membership
from homefabric.accounts.services import deactivate_membership
from homefabric.core.redaction import mask_secret_url, redact
from homefabric.maintenance import services as maint
from homefabric.maintenance.models import Task
from homefabric.notifications import services
from homefabric.notifications.adapters import DeliveryError, LogAdapter
from homefabric.notifications.models import (
    DeliveryKind,
    DeliveryState,
    NotificationDelivery,
    NotificationEndpoint,
)
from homefabric.operations import jobs

pytestmark = pytest.mark.django_db
UTC = dt.UTC


@pytest.fixture
def channel(owner):
    return NotificationEndpoint.objects.create(user=owner, provider="log", display_name="Log")


@pytest.fixture(autouse=True)
def clear_log():
    LogAdapter.sent.clear()


def at(y, m, d, h=0, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=UTC)


def make_task(home, owner, due, created=None, **kw):
    task = maint.create_task(home["boiler"], actor=owner, title="Service boiler", due_date=due,
                             **kw)
    if created:
        Task.objects.filter(pk=task.pk).update(created_at=created)
        task.refresh_from_db()
    return task


def test_default_reminder_seven_days_before_at_0900_household_time(home, owner, channel):
    task = make_task(home, owner, dt.date(2025, 7, 20), created=at(2025, 7, 1))
    # 13 July 09:00 BST == 08:00 UTC
    assert services.plan_reminders(at(2025, 7, 13, 7, 59))["individual"] == 0
    assert services.plan_reminders(at(2025, 7, 13, 8, 0))["individual"] == 1
    d = NotificationDelivery.objects.get()
    assert d.task == task and "Service boiler" in d.subject and "Boiler" in d.body
    assert "20 Jul 2025" in d.subject
    assert "https://home.example.test/tasks/" in d.body  # authenticated link, no attachment
    services.dispatch(at(2025, 7, 13, 8, 1))
    d.refresh_from_db()
    assert d.state == DeliveryState.SENT and len(LogAdapter.sent) == 1


def test_first_tick_after_target_and_no_repeat(home, owner, channel):
    make_task(home, owner, dt.date(2025, 7, 20), created=at(2025, 7, 1))
    services.process(at(2025, 7, 13, 8, 4))
    services.process(at(2025, 7, 13, 8, 5))
    services.process(at(2025, 7, 25, 8, 5))  # overdue now: no repeated notification
    assert len(LogAdapter.sent) == 1


def test_a07_concurrent_planners_create_no_duplicate_jobs(home, owner, channel):
    make_task(home, owner, dt.date(2025, 7, 20), created=at(2025, 7, 1))
    for _ in range(3):
        services.plan_reminders(at(2025, 7, 13, 8, 0))
    assert NotificationDelivery.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_a07_concurrent_dispatchers_send_once(home, owner, channel):
    make_task(home, owner, dt.date(2025, 7, 20), created=at(2025, 7, 1))
    services.plan_reminders(at(2025, 7, 13, 8, 0))
    barrier = threading.Barrier(3)

    def worker(n):
        from django.db import connections

        barrier.wait()
        try:
            services.dispatch(at(2025, 7, 13, 8, 1), worker_id=f"w{n}")
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(LogAdapter.sent) == 1
    assert NotificationDelivery.objects.get().state == DeliveryState.SENT


def test_a07_lease_recovers_after_killed_worker(home, owner, channel):
    make_task(home, owner, dt.date(2025, 7, 20), created=at(2025, 7, 1))
    services.plan_reminders(at(2025, 7, 13, 8, 0))
    d = NotificationDelivery.objects.get()
    assert services.claim(d.pk, "dead-worker", at(2025, 7, 13, 8, 1))  # then the worker dies
    assert services.dispatch(at(2025, 7, 13, 8, 2))["sent"] == 0  # lease still valid
    stats = services.dispatch(at(2025, 7, 13, 8, 30))  # lease (5 min) expired
    assert stats["sent"] == 1
    d.refresh_from_db()
    assert d.state == DeliveryState.SENT and d.attempts == 2


def test_a07_downtime_catch_up_one_summary_per_recipient_channel(home, owner, channel,
                                                                 household):
    schedule = maint.create_schedule(home["boiler"], actor=owner, title="Weekly check",
                                     mode="fixed", interval_count=1, interval_unit="week",
                                     anchor_date=dt.date(2025, 1, 1), generate=False)
    maint.generate_for_schedule(schedule, today=dt.date(2025, 3, 1))  # missed weeks overdue
    email = NotificationEndpoint.objects.create(user=owner, provider="log", display_name="Two")
    stats = services.plan_reminders(at(2025, 3, 1, 12), run_id="run1")
    assert stats["summaries"] == 2  # one per channel
    summaries = NotificationDelivery.objects.filter(kind=DeliveryKind.CATCHUP)
    assert {s.endpoint for s in summaries} == {channel, email}
    assert stats["batched"] >= 18  # overdue plus upcoming, folded into the summary
    # A later run must not re-summarise the same reminders.
    assert services.plan_reminders(at(2025, 3, 1, 12, 1), run_id="run2")["summaries"] == 0
    services.dispatch(at(2025, 3, 1, 12, 2))
    assert len(LogAdapter.sent) == 2


def test_missed_reminder_after_downtime_goes_into_summary(home, owner, channel):
    make_task(home, owner, dt.date(2025, 7, 20), created=at(2025, 7, 1))
    stats = services.plan_reminders(at(2025, 7, 15, 10))  # 2 days late
    assert stats["summaries"] == 1 and stats["individual"] == 0


def test_reminders_suppressed_when_task_completed(home, owner, channel):
    task = make_task(home, owner, dt.date(2025, 7, 20), created=at(2025, 7, 1))
    services.plan_reminders(at(2025, 7, 13, 8, 0))
    maint.complete_task(task, actor=owner, expected_version=task.version,
                        completed_on=dt.date(2025, 7, 13))
    services.dispatch(at(2025, 7, 13, 8, 1))
    assert NotificationDelivery.objects.get().state == DeliveryState.SUPPRESSED
    assert not LogAdapter.sent


def test_disabled_schedule_stops_reminders(home, owner, channel):
    s = maint.create_schedule(home["boiler"], actor=owner, title="S", mode="fixed",
                              interval_count=1, interval_unit="year",
                              anchor_date=dt.date(2025, 7, 20), generate=False)
    maint.generate_for_schedule(s, today=dt.date(2025, 7, 1))
    Task.objects.filter(schedule=s).update(created_at=at(2025, 7, 1))
    maint.set_schedule_enabled(s, False, actor=owner)
    assert services.plan_reminders(at(2025, 7, 13, 8))["individual"] == 0


def test_membership_change_honoured(home, owner, editor, channel, household):
    ep = NotificationEndpoint.objects.create(user=editor, provider="log", display_name="E")
    task = make_task(home, owner, dt.date(2025, 7, 20), created=at(2025, 7, 1), assignee=editor)
    services.plan_reminders(at(2025, 7, 13, 8, 0))
    d = NotificationDelivery.objects.get(endpoint=ep)
    assert d.recipient == editor
    Membership.objects.filter(user=editor).update(is_active=False)
    services.dispatch(at(2025, 7, 13, 8, 1))
    d.refresh_from_db()
    assert d.state == DeliveryState.SUPPRESSED
    assert task.pk


def test_unassigned_goes_to_editors_and_owners_not_viewers(home, owner, editor, viewer):
    for u in (owner, editor, viewer):
        NotificationEndpoint.objects.create(user=u, provider="log", display_name=u.username)
    make_task(home, owner, dt.date(2025, 7, 20), created=at(2025, 7, 1))
    services.plan_reminders(at(2025, 7, 13, 8, 0))
    recipients = set(NotificationDelivery.objects.values_list("recipient__username", flat=True))
    assert recipients == {"owner", "editor"}


def test_paused_delivery_sends_nothing(home, owner, channel):
    make_task(home, owner, dt.date(2025, 7, 20), created=at(2025, 7, 1))
    services.plan_reminders(at(2025, 7, 13, 8, 0))
    services.pause("test")
    assert services.dispatch(at(2025, 7, 13, 8, 1)).get("paused")
    assert not LogAdapter.sent
    services.resume(discard_backlog=False, public_url_verified=True)
    services.dispatch(at(2025, 7, 13, 8, 2))
    assert len(LogAdapter.sent) == 1


class FlakyAdapter:
    calls = 0
    fail_times = 99

    def send(self, endpoint, message):
        FlakyAdapter.calls += 1
        if FlakyAdapter.calls <= FlakyAdapter.fail_times:
            raise DeliveryError("Connection refused to https://user:hunter2@hooks.example/x "
                                f"token={endpoint.resolve_secret()}")


@override_settings(NOTIFICATION_ADAPTERS={"log": "tests.test_notifications.FlakyAdapter"},
                   NOTIFY_MAX_ATTEMPTS=5, NOTIFY_BACKOFF_BASE_SECONDS=60)
def test_a08_outage_bounded_retries_visible_failure_and_redaction(home, owner):
    FlakyAdapter.calls, FlakyAdapter.fail_times = 0, 99
    ep = NotificationEndpoint.objects.create(user=owner, provider="log", display_name="Hook",
                                             secret_value="tgram://SECRETTOKEN123/456")
    make_task(home, owner, dt.date(2025, 7, 20), created=at(2025, 7, 1))
    services.plan_reminders(at(2025, 7, 13, 8, 0))
    now = at(2025, 7, 13, 8, 0)
    delays = []
    for _ in range(10):
        d = NotificationDelivery.objects.get()
        if d.state != DeliveryState.PENDING:
            break
        now = max(now, d.next_attempt_at)
        services.dispatch(now)
        d.refresh_from_db()
        if d.state == DeliveryState.PENDING:
            delays.append((d.next_attempt_at - now).total_seconds())
    d = NotificationDelivery.objects.get()
    assert d.state == DeliveryState.FAILED and d.attempts == 5 and FlakyAdapter.calls == 5
    assert delays == [60, 120, 240, 480]  # bounded exponential backoff
    assert "hunter2" not in d.last_error and "SECRETTOKEN123" not in d.last_error
    assert list(services.failures_for_user(owner)) == [d]
    assert ep.pk


@override_settings(NOTIFICATION_ADAPTERS={"log": "tests.test_notifications.FlakyAdapter"})
def test_a08_recovery_after_outage(home, owner):
    FlakyAdapter.calls, FlakyAdapter.fail_times = 0, 2
    NotificationEndpoint.objects.create(user=owner, provider="log", display_name="Hook")
    make_task(home, owner, dt.date(2025, 7, 20), created=at(2025, 7, 1))
    services.plan_reminders(at(2025, 7, 13, 8, 0))
    now = at(2025, 7, 13, 8, 0)
    for _ in range(5):
        services.dispatch(now)
        now += dt.timedelta(hours=1)
    assert NotificationDelivery.objects.get().state == DeliveryState.SENT


def test_test_send_records_result(owner, channel):
    ok, err = services.send_test(channel, actor=owner)
    assert ok and not err
    channel.refresh_from_db()
    assert channel.last_test_ok
    bad = NotificationEndpoint.objects.create(user=owner, provider="nope", display_name="Bad")
    ok, err = services.send_test(bad, actor=owner)
    assert not ok and "not enabled" in err


def test_masking_and_redaction():
    assert mask_secret_url("tgram://123456:ABCDEF/987654") .startswith("tgram://••••")
    assert "ABCDEF" not in mask_secret_url("tgram://123456:ABCDEF/987654")
    text = redact("failed for mailto://bob:pa55@example.com and ntfy://topic password=abc")
    assert "pa55" not in text and "abc" not in text and "topic" not in text


def test_logging_filter_redacts(caplog):
    import logging

    from homefabric.core.logging import RedactingFilter

    logger = logging.getLogger("redaction-test")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.WARNING, logger="redaction-test"):
        logger.warning("Using %s", "discord://webhook_id/webhook_token")
    assert "webhook_token" not in caplog.text


def test_endpoint_secret_never_rendered(owner, client, household):
    from tests.conftest import login

    NotificationEndpoint.objects.create(user=owner, provider="apprise", display_name="Phone",
                                        secret_value="ntfy://supersecrettopic")
    login(client, owner)
    r = client.get("/notifications/")
    assert b"supersecrettopic" not in r.content


def test_scheduler_heartbeat_and_overlap(db):
    assert jobs.acquire_lock("process_notifications", "a", 60)
    assert not jobs.acquire_lock("process_notifications", "b", 60)
    with pytest.raises(jobs.JobSkipped):
        jobs.run_job("process_notifications", lambda: {})
    jobs.release_lock("process_notifications", "a")
    jobs.run_job("process_notifications", lambda: {"ok": 1})
    health = jobs.scheduler_health()
    job = next(j for j in health["jobs"] if j["name"] == "process_notifications")
    assert job["last_success_at"] and not job["stale"]
    assert health["stale"]  # process_schedules has never run


def test_dashboard_warns_when_scheduler_stale(owner_client):
    r = owner_client.get("/dashboard/")
    assert b"Scheduled processing is not running" in r.content


def test_deactivated_membership_unassigned_tasks(home, owner, editor, household):
    task = make_task(home, owner, dt.date(2025, 7, 20), assignee=editor)
    deactivate_membership(Membership.objects.get(user=editor), actor=owner)
    task.refresh_from_db()
    assert task.assignee is None
    assert timezone.now()
