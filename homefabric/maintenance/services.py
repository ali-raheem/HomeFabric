"""Task, history, and schedule rules shared by web views and CLI commands."""

from __future__ import annotations

import datetime as dt

from django.db import IntegrityError, transaction
from django.utils import timezone

from homefabric.accounts.services import assignable_users
from homefabric.core import audit, money
from homefabric.core.concurrency import versioned_update
from homefabric.core.dates import UNITS, household_today
from homefabric.core.errors import ArchivedError, DomainError
from homefabric.maintenance import recurrence
from homefabric.maintenance.models import (
    COMPLETION_EVENT_TYPES,
    UNFINISHED,
    USER_EVENT_TYPES,
    ActivityEntry,
    EventType,
    MaintenanceSchedule,
    RecurrenceMode,
    ReminderRule,
    Task,
    TaskKind,
    TaskStatus,
)

MAX_GENERATE_PER_RUN = 500


# --- helpers --------------------------------------------------------------

def _ensure_entity_editable(entity):
    if entity.is_archived:
        raise ArchivedError(f"'{entity.name}'")


def _check_assignee(household, assignee):
    if assignee is None:
        return
    if not assignable_users(household).filter(pk=assignee.pk).exists():
        raise DomainError(f"{assignee} cannot be assigned work: they are not an active editor "
                          "or owner of this household.")


def _check_not_future(day: dt.date, household, label="date"):
    if day > household_today(household) + dt.timedelta(days=1):
        raise DomainError(f"The {label} cannot be in the future.")


def parse_money(amount, currency: str, household) -> tuple[int | None, str]:
    if amount in (None, ""):
        return None, ""
    currency = (currency or household.currency).upper()
    if len(currency) != 3 or not currency.isalpha():
        raise DomainError("Currency must be a three-letter code such as GBP.")
    try:
        minor = money.to_minor(amount, currency)
    except ValueError as exc:
        raise DomainError(str(exc)) from exc
    if minor < 0:
        raise DomainError("Amounts cannot be negative.")
    return minor, currency


def _suppress_pending(task: Task, reason: str):
    from homefabric.notifications.services import suppress_for_task

    suppress_for_task(task, reason)


# --- history --------------------------------------------------------------

def record_system_activity(entity, event_type, *, actor, title="", notes="", task=None):
    return ActivityEntry.objects.create(
        household_id=entity.household_id, entity=entity, event_type=event_type,
        occurred_on=household_today(entity.household), title=title, notes=notes, task=task,
        author=actor,
    )


def add_activity(entity, *, actor, event_type: str, occurred_on: dt.date, title="", notes="",
                 provider="", amount=None, currency="", occurred_time=None) -> ActivityEntry:
    _ensure_entity_editable(entity)
    if event_type not in USER_EVENT_TYPES:
        raise DomainError("Choose a valid entry type.")
    _check_not_future(occurred_on, entity.household)
    amount_minor, currency = parse_money(amount, currency, entity.household)
    with transaction.atomic():
        entry = ActivityEntry.objects.create(
            household_id=entity.household_id, entity=entity, event_type=event_type,
            occurred_on=occurred_on, occurred_time=occurred_time, title=title.strip()[:200],
            notes=notes, provider=provider.strip()[:150], amount_minor=amount_minor,
            currency=currency, author=actor,
        )
        audit.record(actor, "activity.add", entry, household=entity.household,
                     label=f"{entity.name}: {entry.headline}")
    return entry


def correct_activity(original: ActivityEntry, *, actor, reason: str, occurred_on, title, notes,
                     provider, amount, currency, event_type=None) -> ActivityEntry:
    """Create a superseding entry; the original remains with its author and time."""
    if not reason.strip():
        raise DomainError("Explain what the correction changes.")
    _check_not_future(occurred_on, original.entity.household)
    amount_minor, currency = parse_money(amount, currency, original.entity.household)
    if event_type is None or original.event_type not in USER_EVENT_TYPES + [EventType.BOOKING]:
        event_type = original.event_type
    with transaction.atomic():
        claimed = ActivityEntry.objects.filter(pk=original.pk, superseded=False).update(
            superseded=True)
        if claimed != 1:
            raise DomainError("This entry has already been corrected; correct the newest "
                              "version instead.")
        entry = ActivityEntry.objects.create(
            household_id=original.household_id, entity=original.entity, event_type=event_type,
            occurred_on=occurred_on, occurred_time=original.occurred_time,
            title=title.strip()[:200], notes=notes, provider=provider.strip()[:150],
            amount_minor=amount_minor, currency=currency, task=original.task, author=actor,
            correction_of=original, correction_reason=reason.strip()[:300],
        )
        from homefabric.documents.models import ActivityDocument

        for link in ActivityDocument.objects.filter(activity=original):
            ActivityDocument.objects.create(household_id=link.household_id, activity=entry,
                                            revision=link.revision, attached_by=link.attached_by)
        audit.record(actor, "activity.correct", entry, household=original.entity.household,
                     changes={"corrects": str(original.pk), "reason": reason})
    return entry


def correction_chain(entry: ActivityEntry) -> list[ActivityEntry]:
    """Oldest-first list of versions leading to ``entry``."""
    chain = [entry]
    current = entry
    while current.correction_of_id:
        current = ActivityEntry.objects.select_related("author").get(pk=current.correction_of_id)
        chain.append(current)
    return list(reversed(chain))


# --- reminder rules -------------------------------------------------------

def add_default_reminder(*, task=None, schedule=None) -> ReminderRule:
    owner = task or schedule
    return ReminderRule.objects.create(household_id=owner.household_id, task=task,
                                       schedule=schedule, lead_days=7, time_of_day=dt.time(9, 0))


def effective_reminder_rules(task: Task) -> list[ReminderRule]:
    if not task.reminders_enabled:
        return []
    rules = [r for r in task.reminder_rules.all() if r.enabled]
    if task.schedule_id:
        rules += [r for r in task.schedule.reminder_rules.all() if r.enabled]
    return rules


# --- tasks ----------------------------------------------------------------

def create_task(entity, *, actor, title: str, description="", due_date=None, assignee=None,
                kind=TaskKind.GENERAL, related_task=None, reminders=True,
                source_activity=None) -> Task:
    _ensure_entity_editable(entity)
    _check_assignee(entity.household, assignee)
    if related_task is not None and related_task.household_id != entity.household_id:
        raise DomainError("Related tasks must be in the same household.")
    if not title.strip():
        raise DomainError("Give the task a title.")
    with transaction.atomic():
        task = Task.objects.create(
            household_id=entity.household_id, entity=entity, title=title.strip()[:200],
            description=description, due_date=due_date, assignee=assignee, kind=kind,
            related_task=related_task, reminders_enabled=reminders, created_by=actor,
            source_activity=source_activity,
        )
        if reminders:
            add_default_reminder(task=task)
        audit.record(actor, "task.create", task, household=entity.household)
    return task


def _ensure_unfinished(task: Task):
    if not task.is_unfinished:
        raise DomainError(f"This task is already {task.get_status_display().lower()}. "
                          "Create a follow-up task or correct the history entry instead.")


def update_task(task: Task, *, actor, expected_version: int, title: str, description: str,
                due_date, assignee, related_task=None, reminders_enabled=None) -> None:
    _ensure_unfinished(task)
    if assignee is not None and assignee.pk != task.assignee_id:
        _check_assignee(task.household, assignee)
    if task.schedule_id and due_date is None:
        raise DomainError("Scheduled tasks need a due date.")
    if related_task is not None and (related_task.pk == task.pk
                                     or related_task.household_id != task.household_id):
        raise DomainError("Choose a different related task.")
    data = {"title": title.strip()[:200], "description": description, "due_date": due_date,
            "assignee": assignee, "related_task": related_task}
    if reminders_enabled is not None:
        data["reminders_enabled"] = reminders_enabled
    changes = audit.diff(task, data, data.keys())
    with transaction.atomic():
        # Changing one occurrence's due date never moves the schedule anchor.
        versioned_update(task, expected_version, **data)
        audit.record(actor, "task.update", task, changes=changes)


def start_task(task: Task, *, actor, expected_version: int) -> None:
    if task.status != TaskStatus.OPEN:
        raise DomainError("Only open tasks can be started.")
    with transaction.atomic():
        versioned_update(task, expected_version, status=TaskStatus.IN_PROGRESS,
                         started_at=timezone.now())
        audit.record(actor, "task.start", task)


def complete_task(task: Task, *, actor, expected_version: int, completed_on: dt.date,
                  event_type: str | None = None, notes="", provider="", amount=None,
                  currency="", uploads=(), revisions=()) -> ActivityEntry:
    """Complete a task, recording a linked history entry with evidence.

    Completing a booking task records only the booking: any related service
    task is untouched.
    """
    from homefabric.documents import services as docs

    _ensure_unfinished(task)
    household = task.household
    _check_not_future(completed_on, household, "completion date")
    if task.kind == TaskKind.BOOKING:
        event_type = EventType.BOOKING
    elif event_type is None:
        event_type = EventType.SERVICE if task.schedule_id else EventType.WORK
    if event_type not in COMPLETION_EVENT_TYPES:
        raise DomainError("Choose a valid kind of work.")
    amount_minor, currency = parse_money(amount, currency, household)
    with transaction.atomic():
        versioned_update(task, expected_version, status=TaskStatus.COMPLETED,
                         completed_at=timezone.now(), completed_on=completed_on,
                         completed_by=actor, result=notes)
        entry = ActivityEntry.objects.create(
            household_id=task.household_id, entity=task.entity, event_type=event_type,
            occurred_on=completed_on, title=task.title, notes=notes,
            provider=provider.strip()[:150], amount_minor=amount_minor, currency=currency,
            task=task, author=actor,
        )
        for upload in uploads:
            if upload:
                document = docs.upload_document(
                    household, upload, actor=actor, category="invoice", task=task,
                    display_name=f"{task.title} – {upload.name}"[:200],
                )
                docs.attach_revision_to_activity(document.current_revision, entry, actor=actor)
        for revision in revisions:
            docs.attach_revision_to_activity(revision, entry, actor=actor)
        _suppress_pending(task, "Task completed")
        audit.record(actor, "task.complete", task,
                     changes={"completed_on": completed_on, "activity": str(entry.pk)})
        if task.schedule_id and task.schedule.mode == RecurrenceMode.AFTER_COMPLETION:
            schedule = MaintenanceSchedule.objects.get(pk=task.schedule_id)
            if schedule.enabled:
                next_due = recurrence.next_after_completion(
                    completed_on, schedule.interval_count, schedule.interval_unit)
                _create_completion_occurrence(schedule, next_due, actor=actor)
    return entry


def _close_with_reason(task, *, actor, expected_version, status, reason, event_type, action):
    with transaction.atomic():
        versioned_update(task, expected_version, status=status, status_reason=reason[:300],
                         completed_at=timezone.now(), completed_by=actor)
        record_system_activity(task.entity, event_type, actor=actor, title=task.title,
                               notes=reason, task=task)
        _suppress_pending(task, f"Task {status}")
        audit.record(actor, action, task, changes={"reason": reason})


def _after_completion_follow_on(task, *, actor, new_due_date, pause_schedule, verb):
    schedule = task.schedule
    if not (schedule and schedule.mode == RecurrenceMode.AFTER_COMPLETION and schedule.enabled):
        return
    if new_due_date is None and not pause_schedule:
        raise DomainError(f"This schedule repeats after completion. To {verb} this occurrence, "
                          "give the next due date or pause the schedule.")
    if pause_schedule:
        set_schedule_enabled(schedule, False, actor=actor, allow_archived=True)
    else:
        _create_completion_occurrence(schedule, new_due_date, actor=actor)


def skip_task(task: Task, *, actor, expected_version: int, reason: str, new_due_date=None,
              pause_schedule=False) -> None:
    """Skip with a reason. Creates a history event, never a service record."""
    _ensure_unfinished(task)
    if not reason.strip():
        raise DomainError("A reason is required to skip a task.")
    with transaction.atomic():
        _close_with_reason(task, actor=actor, expected_version=expected_version,
                           status=TaskStatus.SKIPPED, reason=reason.strip(),
                           event_type=EventType.TASK_SKIPPED, action="task.skip")
        _after_completion_follow_on(task, actor=actor, new_due_date=new_due_date,
                                    pause_schedule=pause_schedule, verb="skip")


def cancel_task(task: Task, *, actor, reason: str, expected_version: int | None = None,
                new_due_date=None, pause_schedule=False, allow_archived=False) -> None:
    _ensure_unfinished(task)
    if not allow_archived:
        _ensure_entity_editable(task.entity)
    if not reason.strip():
        raise DomainError("A reason is required to cancel a task.")
    version = task.version if expected_version is None else expected_version
    with transaction.atomic():
        _close_with_reason(task, actor=actor, expected_version=version,
                           status=TaskStatus.CANCELLED, reason=reason.strip(),
                           event_type=EventType.TASK_CANCELLED, action="task.cancel")
        if not allow_archived:
            _after_completion_follow_on(task, actor=actor, new_due_date=new_due_date,
                                        pause_schedule=pause_schedule, verb="cancel")


def bulk_skip(tasks, *, actor, reason: str) -> int:
    if not reason.strip():
        raise DomainError("A reason is required to skip tasks.")
    count = 0
    with transaction.atomic():
        for task in tasks:
            if not task.is_unfinished:
                continue
            if task.schedule and task.schedule.mode == RecurrenceMode.AFTER_COMPLETION:
                raise DomainError("Bulk skip applies to fixed-calendar schedules only.")
            skip_task(task, actor=actor, expected_version=task.version, reason=reason)
            count += 1
    return count


def create_follow_up(task: Task, *, actor, title: str, due_date=None, description="") -> Task:
    return create_task(task.entity, actor=actor, title=title, description=description,
                       due_date=due_date, related_task=task)


# --- schedules ------------------------------------------------------------

def _validate_schedule_fields(mode, interval_count, interval_unit, horizon_days=None):
    if mode not in RecurrenceMode.values:
        raise DomainError("Choose a recurrence mode.")
    if interval_unit not in UNITS:
        raise DomainError("Choose an interval unit.")
    if not interval_count or interval_count < 1 or interval_count > 1000:
        raise DomainError("The interval must be between 1 and 1000.")
    if horizon_days is not None and not (1 <= horizon_days <= 3660):
        raise DomainError("The generation horizon must be between 1 and 3660 days.")


def create_schedule(entity, *, actor, title: str, mode: str, interval_count: int,
                    interval_unit: str, anchor_date: dt.date, description="",
                    default_assignee=None, horizon_days=None, reminders=True,
                    generate=True) -> MaintenanceSchedule:
    _ensure_entity_editable(entity)
    _validate_schedule_fields(mode, interval_count, interval_unit, horizon_days)
    _check_assignee(entity.household, default_assignee)
    with transaction.atomic():
        schedule = MaintenanceSchedule.objects.create(
            household_id=entity.household_id, entity=entity, title=title.strip()[:200],
            description=description, mode=mode, interval_count=interval_count,
            interval_unit=interval_unit, anchor_date=anchor_date,
            timezone=entity.household.timezone, horizon_days=horizon_days,
            default_assignee=default_assignee, created_by=actor,
        )
        if reminders:
            add_default_reminder(schedule=schedule)
        audit.record(actor, "schedule.create", schedule, household=entity.household)
        if generate:
            generate_for_schedule(schedule)
    return schedule


def _new_occurrence(schedule, *, key: str, scheduled: dt.date, due: dt.date, overdue: bool,
                    actor=None) -> Task | None:
    assignee = schedule.default_assignee
    if assignee is not None and not assignable_users(schedule.household).filter(
            pk=assignee.pk).exists():
        assignee = None
    try:
        with transaction.atomic():
            return Task.objects.create(
                household_id=schedule.household_id, entity_id=schedule.entity_id,
                schedule=schedule, occurrence_key=key, scheduled_date=scheduled, due_date=due,
                title=schedule.title, description=schedule.description, assignee=assignee,
                generated_as_overdue=overdue, created_by=actor,
            )
    except IntegrityError:
        return None  # Another worker already generated this occurrence.


def _create_completion_occurrence(schedule, due: dt.date, *, actor=None) -> Task | None:
    if Task.objects.filter(schedule=schedule, status__in=UNFINISHED).exists():
        return None
    seq = schedule.next_sequence
    task = _new_occurrence(schedule, key=f"seq-{seq}", scheduled=due, due=due, overdue=False,
                           actor=actor)
    MaintenanceSchedule.objects.filter(pk=schedule.pk).update(
        next_sequence=seq + 1, last_generated_at=timezone.now())
    schedule.next_sequence = seq + 1
    return task


def generate_for_schedule(schedule: MaintenanceSchedule, *, today: dt.date | None = None,
                          actor=None) -> list[Task]:
    """Generate due occurrences. Safe to run concurrently and repeatedly."""
    if not schedule.enabled:
        return []
    today = today or household_today(schedule.household)
    created: list[Task] = []
    with transaction.atomic():
        schedule = MaintenanceSchedule.objects.select_for_update().select_related(
            "household").get(pk=schedule.pk)
        if not schedule.enabled:
            return []
        if schedule.mode == RecurrenceMode.AFTER_COMPLETION:
            if not Task.objects.filter(schedule=schedule).exists():
                task = _create_completion_occurrence(schedule, schedule.anchor_date, actor=actor)
                if task:
                    created.append(task)
            return created
        horizon_end = today + dt.timedelta(days=schedule.effective_horizon)
        next_index = schedule.next_index
        for index, day in recurrence.fixed_occurrences(
            schedule.anchor_date, schedule.interval_count, schedule.interval_unit,
            start_index=schedule.next_index, until=horizon_end, limit=MAX_GENERATE_PER_RUN,
        ):
            task = _new_occurrence(schedule, key=day.isoformat(), scheduled=day, due=day,
                                   overdue=day < today, actor=actor)
            if task:
                created.append(task)
            next_index = index + 1
        MaintenanceSchedule.objects.filter(pk=schedule.pk).update(
            next_index=next_index, last_generated_at=timezone.now())
    return created


def preview_dates(*, mode, anchor_date, interval_count, interval_unit, count=6,
                  from_date: dt.date | None = None, last_completed: dt.date | None = None):
    """Upcoming due dates for the recurrence preview."""
    if mode == RecurrenceMode.AFTER_COMPLETION:
        start = last_completed or anchor_date
        dates = [start] if last_completed is None else []
        current = start
        while len(dates) < count:
            current = recurrence.next_after_completion(current, interval_count, interval_unit)
            dates.append(current)
        return dates
    start = 0
    if from_date:
        start = recurrence.first_index_on_or_after(anchor_date, interval_count, interval_unit,
                                                   from_date)
    return [d for _, d in recurrence.fixed_occurrences(
        anchor_date, interval_count, interval_unit, start_index=start, limit=count)]


def _changeable_occurrences(schedule, today):
    """Unstarted future occurrences, which schedule edits may replace."""
    return Task.objects.filter(
        schedule=schedule, status=TaskStatus.OPEN, due_date__gte=today
    ).order_by("due_date")


def preview_schedule_change(schedule, *, data: dict, today: dt.date | None = None) -> dict:
    today = today or household_today(schedule.household)
    affected = list(_changeable_occurrences(schedule, today))
    mode = data.get("mode", schedule.mode)
    anchor = data.get("anchor_date", schedule.anchor_date)
    count = data.get("interval_count", schedule.interval_count)
    unit = data.get("interval_unit", schedule.interval_unit)
    horizon = data.get("horizon_days") or schedule.effective_horizon
    if mode == RecurrenceMode.FIXED:
        start = recurrence.first_index_on_or_after(anchor, count, unit, today)
        new_dates = [d for _, d in recurrence.fixed_occurrences(
            anchor, count, unit, start_index=start, until=today + dt.timedelta(days=horizon))]
    else:
        last = Task.objects.filter(schedule=schedule, status=TaskStatus.COMPLETED).exclude(
            completed_on=None).order_by("-completed_on").values_list("completed_on", flat=True).first()
        new_dates = [recurrence.next_after_completion(last, count, unit) if last else anchor]
    return {"affected": affected, "new_dates": new_dates}


def update_schedule(schedule: MaintenanceSchedule, *, actor, expected_version: int,
                    data: dict, today: dt.date | None = None) -> dict:
    """Apply edits to unstarted future occurrences only; preserve everything else."""
    _ensure_entity_editable(schedule.entity)
    today = today or household_today(schedule.household)
    fields = ["title", "description", "mode", "interval_count", "interval_unit", "anchor_date",
              "default_assignee", "horizon_days"]
    data = {k: v for k, v in data.items() if k in fields}
    _validate_schedule_fields(data.get("mode", schedule.mode),
                              data.get("interval_count", schedule.interval_count),
                              data.get("interval_unit", schedule.interval_unit),
                              data.get("horizon_days"))
    if "default_assignee" in data and data["default_assignee"] != schedule.default_assignee:
        _check_assignee(schedule.household, data["default_assignee"])
    changes = audit.diff(schedule, data, data.keys())
    with transaction.atomic():
        affected = list(_changeable_occurrences(schedule, today))
        removed = [(t.title, t.due_date) for t in affected]
        from homefabric.notifications.models import NotificationDelivery
        NotificationDelivery.objects.filter(task__in=affected).update(task=None)
        Task.objects.filter(pk__in=[t.pk for t in affected]).delete()
        mode = data.get("mode", schedule.mode)
        extra = {}
        if mode == RecurrenceMode.FIXED:
            anchor = data.get("anchor_date", schedule.anchor_date)
            start = recurrence.first_index_on_or_after(
                anchor, data.get("interval_count", schedule.interval_count),
                data.get("interval_unit", schedule.interval_unit), today)
            extra["next_index"] = start
        versioned_update(schedule, expected_version, **data, **extra)
        created = []
        if mode == RecurrenceMode.FIXED:
            created = generate_for_schedule(schedule, today=today, actor=actor)
        elif schedule.enabled and not Task.objects.filter(
                schedule=schedule, status__in=UNFINISHED).exists():
            new = preview_schedule_change(schedule, data={}, today=today)["new_dates"]
            task = _create_completion_occurrence(schedule, new[0], actor=actor)
            created = [task] if task else []
        audit.record(actor, "schedule.update", schedule,
                     changes={**changes, "replaced_occurrences": len(removed),
                              "new_occurrences": len(created)})
    return {"removed": removed, "created": created}


def catch_up_preview(schedule: MaintenanceSchedule, today: dt.date | None = None) -> dict:
    """What re-enabling would generate: missed (overdue) and upcoming occurrences."""
    today = today or household_today(schedule.household)
    if schedule.mode == RecurrenceMode.AFTER_COMPLETION:
        has_open = Task.objects.filter(schedule=schedule, status__in=UNFINISHED).exists()
        return {"missed": [], "upcoming": [], "needs_due_date": not has_open}
    horizon_end = today + dt.timedelta(days=schedule.effective_horizon)
    dates = [d for _, d in recurrence.fixed_occurrences(
        schedule.anchor_date, schedule.interval_count, schedule.interval_unit,
        start_index=schedule.next_index, until=horizon_end, limit=MAX_GENERATE_PER_RUN)]
    return {"missed": [d for d in dates if d < today],
            "upcoming": [d for d in dates if d >= today], "needs_due_date": False}


def set_schedule_enabled(schedule: MaintenanceSchedule, enabled: bool, *, actor,
                         skip_missed=False, next_due_date=None, allow_archived=False,
                         today: dt.date | None = None) -> list[Task]:
    if not allow_archived:
        _ensure_entity_editable(schedule.entity)
    today = today or household_today(schedule.household)
    created = []
    with transaction.atomic():
        if not enabled:
            MaintenanceSchedule.objects.filter(pk=schedule.pk).update(
                enabled=False, disabled_at=timezone.now())
            schedule.enabled = False
            from homefabric.notifications.services import suppress_for_schedule

            suppress_for_schedule(schedule, "Schedule disabled")
            audit.record(actor, "schedule.disable", schedule)
            return []
        update = {"enabled": True, "disabled_at": None}
        if schedule.mode == RecurrenceMode.FIXED and skip_missed:
            update["next_index"] = max(schedule.next_index, recurrence.first_index_on_or_after(
                schedule.anchor_date, schedule.interval_count, schedule.interval_unit, today))
        MaintenanceSchedule.objects.filter(pk=schedule.pk).update(**update)
        schedule.refresh_from_db()
        if schedule.mode == RecurrenceMode.AFTER_COMPLETION:
            if not Task.objects.filter(schedule=schedule, status__in=UNFINISHED).exists():
                if next_due_date is None and Task.objects.filter(schedule=schedule).exists():
                    raise DomainError("Give the next due date to resume this schedule.")
                due = next_due_date or schedule.anchor_date
                task = _create_completion_occurrence(schedule, due, actor=actor)
                created = [task] if task else []
        else:
            created = generate_for_schedule(schedule, today=today, actor=actor)
        audit.record(actor, "schedule.enable", schedule,
                     changes={"skip_missed": skip_missed, "generated": len(created)})
    return created


def run_all_schedules(now=None) -> dict:
    """Generate occurrences for every enabled schedule (scheduler entry point)."""
    created = 0
    schedules = 0
    errors = []
    for schedule in MaintenanceSchedule.objects.filter(enabled=True).select_related("household"):
        schedules += 1
        try:
            created += len(generate_for_schedule(schedule))
        except Exception as exc:  # noqa: BLE001 - one bad schedule must not stop the rest
            errors.append(f"{schedule.pk}: {exc}")
    return {"schedules": schedules, "created": created, "errors": errors}


def task_counts(household, today: dt.date) -> dict:
    qs = Task.objects.filter(household=household, status__in=UNFINISHED)
    return {
        "overdue": qs.filter(due_date__lt=today).count(),
        "due_soon": qs.filter(due_date__gte=today, due_date__lte=today + dt.timedelta(days=14)).count(),
        "open": qs.count(),
    }
