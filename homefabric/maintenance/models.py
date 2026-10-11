import datetime as dt
import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse

from homefabric.core.models import HouseholdOwned, TimeStampedModel, Versioned


class EventType(models.TextChoices):
    NOTE = "note", "Note"
    OBSERVATION = "observation", "Observation"
    SERVICE = "service", "Service"
    REPAIR = "repair", "Repair"
    INSPECTION = "inspection", "Inspection"
    INSTALLATION = "installation", "Installation"
    PURCHASE = "purchase", "Purchase"
    BOOKING = "booking", "Booking"
    WORK = "work", "Work completed"
    TASK_SKIPPED = "task_skipped", "Task skipped"
    TASK_CANCELLED = "task_cancelled", "Task cancelled"
    ARCHIVED = "archived", "Archived"
    RESTORED = "restored", "Restored"


USER_EVENT_TYPES = [
    EventType.NOTE, EventType.OBSERVATION, EventType.SERVICE, EventType.REPAIR,
    EventType.INSPECTION, EventType.INSTALLATION, EventType.PURCHASE, EventType.WORK,
]
COMPLETION_EVENT_TYPES = [
    EventType.SERVICE, EventType.REPAIR, EventType.INSPECTION, EventType.INSTALLATION,
    EventType.WORK, EventType.BOOKING,
]


class ActivityEntry(HouseholdOwned):
    """Dated service history. Entries are never edited in place: a correction
    creates a new entry that supersedes the original, which stays readable."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey("assets.Entity", on_delete=models.PROTECT, related_name="activity")
    event_type = models.CharField(max_length=20, choices=EventType.choices, db_index=True)
    occurred_on = models.DateField(db_index=True)
    occurred_time = models.TimeField(null=True, blank=True)
    title = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    provider = models.CharField(max_length=150, blank=True)
    amount_minor = models.BigIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    task = models.ForeignKey(
        "Task", null=True, blank=True, on_delete=models.SET_NULL, related_name="activity"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    correction_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="corrections"
    )
    correction_reason = models.CharField(max_length=300, blank=True)
    superseded = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-occurred_on", "-created_at"]
        verbose_name_plural = "activity entries"
        indexes = [models.Index(fields=["entity", "superseded", "occurred_on"])]

    def __str__(self):
        return f"{self.occurred_on} {self.get_event_type_display()}: {self.title or self.entity}"

    @property
    def headline(self) -> str:
        return self.title or self.get_event_type_display()


class TaskStatus(models.TextChoices):
    OPEN = "open", "Open"
    IN_PROGRESS = "in_progress", "In progress"
    COMPLETED = "completed", "Completed"
    SKIPPED = "skipped", "Skipped"
    CANCELLED = "cancelled", "Cancelled"


UNFINISHED = [TaskStatus.OPEN, TaskStatus.IN_PROGRESS]


class TaskKind(models.TextChoices):
    GENERAL = "general", "Task"
    BOOKING = "booking", "Booking"


class Task(HouseholdOwned, TimeStampedModel, Versioned):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey("assets.Entity", on_delete=models.PROTECT, related_name="tasks")
    schedule = models.ForeignKey(
        "MaintenanceSchedule", null=True, blank=True, on_delete=models.PROTECT,
        related_name="tasks",
    )
    occurrence_key = models.CharField(max_length=40, blank=True)
    scheduled_date = models.DateField(null=True, blank=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=10, choices=TaskKind.choices, default=TaskKind.GENERAL)
    related_task = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="related_from"
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="assigned_tasks",
    )
    status = models.CharField(
        max_length=12, choices=TaskStatus.choices, default=TaskStatus.OPEN, db_index=True
    )
    due_date = models.DateField(null=True, blank=True, db_index=True)
    reminders_enabled = models.BooleanField(default=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_on = models.DateField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    result = models.TextField(blank=True)
    status_reason = models.CharField(max_length=300, blank=True)
    generated_as_overdue = models.BooleanField(default=False)
    source_activity = models.ForeignKey(
        ActivityEntry, null=True, blank=True, on_delete=models.SET_NULL, related_name="follow_ups"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["due_date", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["schedule", "occurrence_key"], condition=models.Q(schedule__isnull=False),
                name="unique_schedule_occurrence",
            )
        ]
        indexes = [models.Index(fields=["household", "status", "due_date"])]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("maintenance:task_detail", args=[self.pk])

    @property
    def is_unfinished(self) -> bool:
        return self.status in UNFINISHED

    def is_overdue(self, today: dt.date) -> bool:
        return self.is_unfinished and self.due_date is not None and self.due_date < today


class RecurrenceMode(models.TextChoices):
    FIXED = "fixed", "Fixed calendar"
    AFTER_COMPLETION = "after_completion", "After completion"


class IntervalUnit(models.TextChoices):
    DAY = "day", "Days"
    WEEK = "week", "Weeks"
    MONTH = "month", "Months"
    YEAR = "year", "Years"


class MaintenanceSchedule(HouseholdOwned, TimeStampedModel, Versioned):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey("assets.Entity", on_delete=models.PROTECT, related_name="schedules")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    mode = models.CharField(max_length=20, choices=RecurrenceMode.choices)
    interval_count = models.PositiveIntegerField(default=1)
    interval_unit = models.CharField(max_length=5, choices=IntervalUnit.choices)
    anchor_date = models.DateField()
    timezone = models.CharField(max_length=64)
    horizon_days = models.PositiveIntegerField(null=True, blank=True)
    # Fixed mode: index of the next occurrence to generate (from the anchor).
    next_index = models.PositiveIntegerField(default=0)
    # After-completion mode: sequence number of the next occurrence.
    next_sequence = models.PositiveIntegerField(default=1)
    enabled = models.BooleanField(default=True, db_index=True)
    disabled_at = models.DateTimeField(null=True, blank=True)
    default_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    last_generated_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("maintenance:schedule_detail", args=[self.pk])

    @property
    def describe(self) -> str:
        unit = self.get_interval_unit_display().lower()
        if self.interval_count == 1:
            unit = unit.rstrip("s")
            every = f"Every {unit}"
        else:
            every = f"Every {self.interval_count} {unit}"
        if self.mode == RecurrenceMode.AFTER_COMPLETION:
            return f"{every} after the last completion"
        return f"{every} from {self.anchor_date:%d %b %Y}"

    @property
    def effective_horizon(self) -> int:
        return self.horizon_days or settings.SCHEDULE_HORIZON_DAYS


class RecipientMode(models.TextChoices):
    ASSIGNEE_OR_MEMBERS = "assignee_or_members", "Assignee, or all editors and owners if unassigned"
    ALL_MEMBERS = "all_members", "All editors and owners"
    SPECIFIC = "specific", "Specific people"


class ReminderRule(HouseholdOwned):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(
        Task, null=True, blank=True, on_delete=models.CASCADE, related_name="reminder_rules"
    )
    schedule = models.ForeignKey(
        MaintenanceSchedule, null=True, blank=True, on_delete=models.CASCADE,
        related_name="reminder_rules",
    )
    lead_days = models.PositiveIntegerField(default=7)
    time_of_day = models.TimeField(default=dt.time(9, 0))
    recipient_mode = models.CharField(
        max_length=24, choices=RecipientMode.choices, default=RecipientMode.ASSIGNEE_OR_MEMBERS
    )
    recipients = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="+")
    # Empty list means every enabled channel of each recipient.
    channels = models.JSONField(default=list, blank=True)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-lead_days", "time_of_day"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(task__isnull=False, schedule__isnull=True)
                    | models.Q(task__isnull=True, schedule__isnull=False)
                ),
                name="reminder_rule_has_one_owner",
            )
        ]

    def __str__(self):
        when = "on the due date" if self.lead_days == 0 else f"{self.lead_days} days before"
        return f"{when} at {self.time_of_day:%H:%M}"
