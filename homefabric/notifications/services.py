"""Persistent notification outbox: planning, leasing, delivery, and retries.

Delivery is at-least-once towards providers: a provider that times out after
accepting a message may cause a duplicate on retry. Exactly-once external
delivery is not claimed.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections import defaultdict

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

from homefabric.accounts.models import Membership, Role
from homefabric.core import audit
from homefabric.core.dates import get_zone, local_to_utc
from homefabric.core.redaction import redact
from homefabric.notifications.adapters import Message, get_adapter
from homefabric.notifications.models import (
    ACTIVE_STATES,
    DeliveryKind,
    DeliveryState,
    NotificationDelivery,
    NotificationEndpoint,
)

log = logging.getLogger("homefabric.notifications")


# --- state ----------------------------------------------------------------

def is_paused() -> bool:
    from homefabric.operations.models import SystemState

    return SystemState.get().notifications_paused


def pause(reason: str, *, actor=None) -> None:
    from homefabric.operations.models import SystemState

    state = SystemState.get()
    state.notifications_paused = True
    state.paused_reason = reason[:300]
    state.paused_at = timezone.now()
    state.save()
    audit.record(actor, "notifications.pause", state, changes={"reason": reason},
                 label="Notifications")


def resume_preview() -> dict:
    now = timezone.now()
    pending = NotificationDelivery.objects.filter(state=DeliveryState.PENDING)
    return {
        "pending": pending.count(),
        "overdue": pending.filter(due_at__lt=now - dt.timedelta(hours=1)).count(),
        "review": NotificationDelivery.objects.filter(state=DeliveryState.REVIEW).count(),
        "endpoints_needing_verification": NotificationEndpoint.objects.filter(
            needs_verification=True, deleted=False).count(),
    }


def resume(*, actor=None, discard_backlog: bool, public_url_verified: bool) -> dict:
    """Resume delivery. Requires the operator to confirm the public URL."""
    from homefabric.operations.models import SystemState

    if not public_url_verified:
        raise ValueError("Confirm that the public URL in notification links is correct.")
    discarded = 0
    with transaction.atomic():
        if discard_backlog:
            discarded = NotificationDelivery.objects.filter(
                state__in=[DeliveryState.PENDING, DeliveryState.REVIEW]
            ).update(state=DeliveryState.SUPPRESSED, last_error="Discarded when resuming "
                     "notifications", lease_owner="", lease_expires_at=None)
        else:
            NotificationDelivery.objects.filter(state=DeliveryState.REVIEW).update(
                state=DeliveryState.SUPPRESSED,
                last_error="Was in flight during backup/restore; not re-sent automatically")
        state = SystemState.get()
        state.notifications_paused = False
        state.paused_reason = ""
        state.public_url_verified = True
        state.save()
        audit.record(actor, "notifications.resume", state,
                     changes={"discarded": discarded}, label="Notifications")
    return {"discarded": discarded}


# --- suppression ----------------------------------------------------------

def suppress_for_task(task, reason: str) -> int:
    return NotificationDelivery.objects.filter(task=task, state=DeliveryState.PENDING).update(
        state=DeliveryState.SUPPRESSED, last_error=reason[:1000])


def suppress_for_schedule(schedule, reason: str) -> int:
    return NotificationDelivery.objects.filter(
        task__schedule=schedule, state=DeliveryState.PENDING
    ).update(state=DeliveryState.SUPPRESSED, last_error=reason[:1000])


# --- recipients and content ----------------------------------------------

def _member_ids(household, roles=None) -> set:
    qs = Membership.objects.filter(household=household, is_active=True, user__is_active=True)
    if roles:
        qs = qs.filter(role__in=roles)
    return set(qs.values_list("user_id", flat=True))


def recipients_for(task, rule) -> set:
    """Resolve recipients at send time so membership changes are honoured."""
    from homefabric.maintenance.models import RecipientMode

    members = _member_ids(task.household)
    workers = _member_ids(task.household, [Role.EDITOR, Role.OWNER])
    if rule.recipient_mode == RecipientMode.SPECIFIC:
        return {u.pk for u in rule.recipients.all()} & members
    if rule.recipient_mode == RecipientMode.ALL_MEMBERS:
        return workers
    if task.assignee_id and task.assignee_id in members:
        return {task.assignee_id}
    return workers


def usable_endpoints(user_ids, channels=None):
    qs = NotificationEndpoint.objects.filter(
        user_id__in=user_ids, enabled=True, deleted=False, needs_verification=False,
        provider__in=list(settings.NOTIFICATION_ADAPTERS),
    )
    if channels:
        qs = qs.filter(provider__in=channels)
    return qs


def task_link(task) -> str:
    return settings.PUBLIC_BASE_URL + reverse("maintenance:task_detail", args=[task.pk])


def reminder_message(task) -> Message:
    due = f"{task.due_date:%a %d %b %Y}" if task.due_date else "no due date"
    subject = f"Reminder: {task.title} ({task.entity.name}) due {due}"
    body = (f"{task.title}\nItem: {task.entity.name}\nDue: {due}\n"
            f"Household: {task.household.name}\n\nOpen in HomeFabric (sign-in required):\n"
            f"{task_link(task)}\n")
    return Message(subject, body)


def catchup_message(tasks, household) -> Message:
    lines = [f"- {t.title} ({t.entity.name}), due {t.due_date:%d %b %Y}: {task_link(t)}"
             for t in tasks]
    subject = f"HomeFabric catch-up: {len(tasks)} reminder(s) for {household.name}"
    body = ("These reminders were due while scheduled processing was not running, or the "
            "work was generated as already overdue.\n\n" + "\n".join(lines)
            + "\n\nSign-in is required to open links.\n")
    return Message(subject, body)


# --- planning -------------------------------------------------------------

def _create_delivery(**fields) -> NotificationDelivery | None:
    try:
        with transaction.atomic():
            return NotificationDelivery.objects.create(**fields)
    except IntegrityError:
        return None  # Already exists: duplicate job creation prevented by unique key.


def plan_reminders(now: dt.datetime | None = None, run_id: str | None = None) -> dict:
    """Create delivery jobs for reminders whose target time has arrived."""
    from homefabric.maintenance.models import UNFINISHED, Task
    from homefabric.maintenance.services import effective_reminder_rules

    now = now or timezone.now()
    run_id = run_id or uuid.uuid4().hex
    grace = dt.timedelta(seconds=settings.NOTIFY_CATCHUP_GRACE_SECONDS)
    stats = {"individual": 0, "summaries": 0, "batched": 0}
    if is_paused():
        stats["paused"] = True
        return stats
    latest_due = (now + dt.timedelta(days=400)).date()
    tasks = (
        Task.objects.filter(status__in=UNFINISHED, reminders_enabled=True,
                            due_date__isnull=False, due_date__lte=latest_due,
                            entity__household__isnull=False)
        .filter(Q(schedule__isnull=True) | Q(schedule__enabled=True))
        .select_related("household", "entity", "schedule", "assignee")
        .prefetch_related("reminder_rules__recipients", "schedule__reminder_rules__recipients")
    )
    existing = set(
        NotificationDelivery.objects.filter(
            task__in=tasks, kind=DeliveryKind.REMINDER
        ).values_list("event_key", "recipient_id", "endpoint_id")
    )
    late: dict[tuple, list] = defaultdict(list)
    timely: dict[tuple, list] = defaultdict(list)
    for task in tasks:
        zone = task.household.timezone
        today = now.astimezone(get_zone(zone)).date()
        for rule in effective_reminder_rules(task):
            target_day = task.due_date - dt.timedelta(days=rule.lead_days)
            target = local_to_utc(target_day, rule.time_of_day, zone)
            if target > now:
                continue
            event_key = f"reminder:{task.pk}:{rule.pk}:{task.due_date.isoformat()}"
            if task.generated_as_overdue:
                is_late = True
            elif target < task.created_at:
                if task.due_date < today:
                    continue  # Created already overdue; nothing to remind about.
                is_late = False
            else:
                is_late = now - target > grace
            recipients = recipients_for(task, rule)
            for endpoint in usable_endpoints(recipients, rule.channels):
                key = (event_key, endpoint.user_id, endpoint.pk)
                if key in existing:
                    continue
                existing.add(key)
                group = (endpoint.user_id, endpoint.pk, task.household_id)
                (late if is_late else timely)[group].append((task, event_key, endpoint, target))
    # A recipient/channel receiving a catch-up summary gets everything due in
    # this run folded into it, so a catch-up run sends one message per channel.
    for group in list(timely):
        if group in late:
            late[group].extend(timely.pop(group))
    for items in timely.values():
        for task, event_key, endpoint, target in items:
            msg = reminder_message(task)
            if _create_delivery(
                household_id=task.household_id, kind=DeliveryKind.REMINDER,
                event_key=event_key, recipient_id=endpoint.user_id, endpoint=endpoint,
                channel=endpoint.provider, task=task, subject=msg.subject[:250],
                body=msg.body, due_at=target, next_attempt_at=now,
            ):
                stats["individual"] += 1
    # At most one catch-up summary per recipient/channel per run.
    for (user_id, endpoint_id, household_id), items in late.items():
        endpoint = items[0][2]
        tasks_in = sorted({t.pk: t for t, _, _, _ in items}.values(),
                          key=lambda t: t.due_date)
        msg = catchup_message(tasks_in, tasks_in[0].household)
        with transaction.atomic():
            summary = _create_delivery(
                household_id=household_id, kind=DeliveryKind.CATCHUP,
                event_key=f"catchup:{run_id}:{household_id}", recipient_id=user_id,
                endpoint_id=endpoint_id, channel=endpoint.provider, subject=msg.subject[:250],
                body=msg.body, due_at=now, next_attempt_at=now,
            )
            if summary is None:
                continue
            stats["summaries"] += 1
            for task, event_key, _, _ in items:
                if _create_delivery(
                    household_id=household_id, kind=DeliveryKind.REMINDER, event_key=event_key,
                    recipient_id=user_id, endpoint_id=endpoint_id, channel=endpoint.provider,
                    task=task, subject=f"(in summary) {task.title}"[:250], body="",
                    due_at=now, next_attempt_at=now, state=DeliveryState.BATCHED, batch=summary,
                ):
                    stats["batched"] += 1
    return stats


# --- dispatch -------------------------------------------------------------

def _backoff(attempts: int) -> dt.timedelta:
    seconds = settings.NOTIFY_BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1))
    return dt.timedelta(seconds=min(seconds, settings.NOTIFY_BACKOFF_MAX_SECONDS))


def claim(delivery_id, worker_id: str, now: dt.datetime) -> bool:
    """Atomically lease one job. Expired leases (killed workers) are reclaimable."""
    lease_until = now + dt.timedelta(seconds=settings.NOTIFY_LEASE_SECONDS)
    claimable = Q(state=DeliveryState.PENDING, next_attempt_at__lte=now) | Q(
        state=DeliveryState.LEASED, lease_expires_at__lt=now)
    updated = NotificationDelivery.objects.filter(claimable, pk=delivery_id).update(
        state=DeliveryState.LEASED, lease_owner=worker_id, lease_expires_at=lease_until,
        attempts=F("attempts") + 1, updated_at=now,
    )
    return updated == 1


def _suppression_reason(delivery: NotificationDelivery) -> str | None:
    if delivery.kind == DeliveryKind.TEST:
        return None
    if not delivery.endpoint.is_usable:
        return "Channel disabled or awaiting verification"
    if delivery.household_id and delivery.recipient_id not in _member_ids(delivery.household):
        return "Recipient is no longer an active household member"
    if delivery.kind == DeliveryKind.REMINDER:
        task = delivery.task
        if task is None or not task.is_unfinished:
            return "Task is no longer open"
        if task.schedule_id and not task.schedule.enabled:
            return "Schedule disabled"
        if not task.reminders_enabled:
            return "Reminders disabled for this task"
    return None


def _finish(delivery, worker_id, **fields):
    NotificationDelivery.objects.filter(pk=delivery.pk, lease_owner=worker_id).update(
        lease_owner="", lease_expires_at=None, **fields)


def deliver_one(delivery: NotificationDelivery, worker_id: str, now: dt.datetime) -> str:
    max_attempts = settings.NOTIFY_MAX_ATTEMPTS
    reason = _suppression_reason(delivery)
    if reason:
        _finish(delivery, worker_id, state=DeliveryState.SUPPRESSED, last_error=reason)
        return "suppressed"
    secret = delivery.endpoint.resolve_secret()
    try:
        get_adapter(delivery.channel).send(delivery.endpoint,
                                           Message(delivery.subject, delivery.body))
    except Exception as exc:  # noqa: BLE001 - adapters may raise anything
        retryable = getattr(exc, "retryable", True)
        error = redact(str(exc) or exc.__class__.__name__, [secret])[:1000]
        log.warning("Delivery %s failed (attempt %s): %s", delivery.pk, delivery.attempts, error)
        if not retryable or delivery.attempts >= max_attempts:
            _finish(delivery, worker_id, state=DeliveryState.FAILED, last_error=error)
            return "failed"
        _finish(delivery, worker_id, state=DeliveryState.PENDING, last_error=error,
                next_attempt_at=now + _backoff(delivery.attempts))
        return "retry"
    _finish(delivery, worker_id, state=DeliveryState.SENT, sent_at=timezone.now(), last_error="")
    if delivery.kind == DeliveryKind.CATCHUP:
        delivery.batched_items.update(sent_at=timezone.now())
    return "sent"


def dispatch(now: dt.datetime | None = None, worker_id: str | None = None,
             limit: int = 200) -> dict:
    now = now or timezone.now()
    worker_id = worker_id or uuid.uuid4().hex
    stats = {"sent": 0, "failed": 0, "retry": 0, "suppressed": 0, "expired": 0}
    if is_paused():
        stats["paused"] = True
        return stats
    candidates = list(NotificationDelivery.objects.filter(
        Q(state=DeliveryState.PENDING, next_attempt_at__lte=now)
        | Q(state=DeliveryState.LEASED, lease_expires_at__lt=now)
    ).order_by("next_attempt_at").values_list("pk", flat=True)[:limit])
    for pk in candidates:
        if not claim(pk, worker_id, now):
            continue  # Another worker holds it.
        delivery = NotificationDelivery.objects.select_related(
            "endpoint", "task", "task__schedule", "household").get(pk=pk)
        if delivery.attempts > settings.NOTIFY_MAX_ATTEMPTS:
            _finish(delivery, worker_id, state=DeliveryState.FAILED,
                    last_error="A worker stopped during delivery and the attempt limit was "
                               "reached; the provider may or may not have delivered it.")
            stats["expired"] += 1
            continue
        stats[deliver_one(delivery, worker_id, now)] += 1
    return stats


def process(now=None) -> dict:
    run_id = uuid.uuid4().hex
    planned = plan_reminders(now, run_id)
    sent = dispatch(now, worker_id=run_id)
    return {"planned": planned, "dispatch": sent}


# --- endpoints ------------------------------------------------------------

def send_test(endpoint: NotificationEndpoint, *, actor) -> tuple[bool, str]:
    """Send a test message synchronously; works even while delivery is paused."""
    now = timezone.now()
    msg = Message(
        "HomeFabric test notification",
        f"This is a test message for '{endpoint.display_name}'.\n"
        f"Instance: {settings.PUBLIC_BASE_URL}\nSent: {now:%Y-%m-%d %H:%M} UTC\n",
    )
    delivery = NotificationDelivery.objects.create(
        kind=DeliveryKind.TEST, event_key=f"test:{uuid.uuid4().hex}", recipient=endpoint.user,
        endpoint=endpoint, channel=endpoint.provider, subject=msg.subject, body=msg.body,
        due_at=now, next_attempt_at=now, state=DeliveryState.LEASED, attempts=1,
        lease_owner="test", lease_expires_at=now + dt.timedelta(minutes=5),
    )
    secret = endpoint.resolve_secret()
    try:
        get_adapter(endpoint.provider).send(endpoint, msg)
    except Exception as exc:  # noqa: BLE001
        error = redact(str(exc) or exc.__class__.__name__, [secret])[:500]
        _finish(delivery, "test", state=DeliveryState.FAILED, last_error=error)
        NotificationEndpoint.objects.filter(pk=endpoint.pk).update(
            last_test_at=now, last_test_ok=False, last_test_error=error)
        return False, error
    _finish(delivery, "test", state=DeliveryState.SENT, sent_at=timezone.now())
    NotificationEndpoint.objects.filter(pk=endpoint.pk).update(
        last_test_at=now, last_test_ok=True, last_test_error="", needs_verification=False)
    audit.record(actor, "endpoint.test", endpoint, label=str(endpoint))
    return True, ""


def failures_for_user(user, household=None):
    qs = NotificationDelivery.objects.filter(
        recipient=user, state=DeliveryState.FAILED, acknowledged=False)
    return qs.select_related("endpoint")


def household_failures(household):
    return NotificationDelivery.objects.filter(
        household=household, state=DeliveryState.FAILED, acknowledged=False
    ).select_related("endpoint", "recipient")


def health_summary(household=None) -> dict:
    qs = NotificationDelivery.objects.all()
    if household is not None:
        qs = qs.filter(household=household)
    since = timezone.now() - dt.timedelta(days=7)
    return {
        "pending": qs.filter(state__in=ACTIVE_STATES).count(),
        "failed_unacknowledged": qs.filter(state=DeliveryState.FAILED, acknowledged=False).count(),
        "sent_7d": qs.filter(state=DeliveryState.SENT, sent_at__gte=since).count(),
        "paused": is_paused(),
    }
