import datetime as dt

from django import forms
from django.contrib.auth import get_user_model

from homefabric.accounts.services import active_member_users, assignable_users
from homefabric.core.forms import DateInput, EntityChoiceField, MoneyMixin, TimeInput, VersionField
from homefabric.documents.filetypes import ACCEPT_ATTR
from homefabric.maintenance.models import (
    COMPLETION_EVENT_TYPES,
    UNFINISHED,
    USER_EVENT_TYPES,
    IntervalUnit,
    RecipientMode,
    RecurrenceMode,
    Task,
    TaskKind,
)


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": ACCEPT_ATTR}))
        kwargs.setdefault("required", False)
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(d, initial) for d in data if d]
        return [single(data, initial)] if data else []


class UserChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.label


class TaskForm(forms.Form):
    entity = EntityChoiceField(queryset=None, label="Item")
    title = forms.CharField(max_length=200)
    kind = forms.ChoiceField(choices=TaskKind.choices, initial=TaskKind.GENERAL,
                             help_text="A booking task records only that a booking was made.")
    related_task = forms.ModelChoiceField(
        queryset=Task.objects.none(), required=False, label="Related task",
        help_text="For a booking, the service task it books. Completing the booking does not "
                  "complete the service.")
    due_date = forms.DateField(required=False, widget=DateInput())
    assignee = UserChoiceField(queryset=None, required=False)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
    reminders = forms.BooleanField(required=False, initial=True,
                                   label="Remind 7 days before at 09:00")
    version = VersionField(required=False)

    def __init__(self, *args, household, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        from homefabric.assets.models import Entity, EntityStatus

        self.fields["entity"].queryset = Entity.objects.filter(
            household=household, status=EntityStatus.ACTIVE).select_related("entity_type")
        self.fields["assignee"].queryset = assignable_users(household)
        self.fields["related_task"].queryset = Task.objects.filter(
            household=household, status__in=UNFINISHED).select_related("entity")
        if editing:
            del self.fields["entity"]
            del self.fields["kind"]
            self.fields["reminders"].label = "Send reminders for this task"


class CompleteForm(MoneyMixin):
    completed_on = forms.DateField(widget=DateInput(), label="Date the work was done")
    event_type = forms.ChoiceField(label="Kind of work")
    provider = forms.CharField(max_length=150, required=False,
                               label="Provider / engineer / company")
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), required=False,
                            help_text="What was done, readings, parts replaced.")
    evidence = MultipleFileField(label="Invoice, certificate or photos",
                                 help_text="Optional. PDF, images or Office documents.")
    version = VersionField()

    def __init__(self, *args, task, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [(c.value, c.label) for c in COMPLETION_EVENT_TYPES]
        if task.kind == TaskKind.BOOKING:
            choices = [("booking", "Booking")]
        self.fields["event_type"].choices = choices


class CloseForm(forms.Form):
    reason = forms.CharField(max_length=300, widget=forms.Textarea(attrs={"rows": 2}))
    new_due_date = forms.DateField(required=False, widget=DateInput(),
                                   label="Next due date")
    pause_schedule = forms.BooleanField(required=False, label="Pause the schedule instead")
    version = VersionField()


class FollowUpForm(forms.Form):
    title = forms.CharField(max_length=200)
    due_date = forms.DateField(required=False, widget=DateInput())
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)


class ScheduleForm(forms.Form):
    title = forms.CharField(max_length=200, help_text="For example 'Annual boiler service'.")
    mode = forms.ChoiceField(choices=RecurrenceMode.choices, widget=forms.RadioSelect,
                             initial=RecurrenceMode.FIXED, help_text=(
                                 "Fixed calendar: due on set dates regardless of when work is "
                                 "done. After completion: next due date counts from the actual "
                                 "completion date."))
    interval_count = forms.IntegerField(min_value=1, max_value=1000, initial=1, label="Every")
    interval_unit = forms.ChoiceField(choices=IntervalUnit.choices, initial=IntervalUnit.YEAR,
                                      label="Unit")
    anchor_date = forms.DateField(widget=DateInput(), label="First due date",
                                  initial=dt.date.today)
    default_assignee = UserChoiceField(queryset=None, required=False)
    horizon_days = forms.IntegerField(required=False, min_value=1, max_value=3660,
                                      label="Generate ahead (days)",
                                      help_text="Default 90. Fixed-calendar schedules only.")
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
    reminders = forms.BooleanField(required=False, initial=True,
                                   label="Remind 7 days before each due date at 09:00")
    version = VersionField(required=False)

    def __init__(self, *args, household, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["default_assignee"].queryset = assignable_users(household)
        if editing:
            del self.fields["reminders"]


class EnableScheduleForm(forms.Form):
    skip_missed = forms.BooleanField(required=False,
                                     label="Skip occurrences missed while disabled")
    next_due_date = forms.DateField(required=False, widget=DateInput(), label="Next due date")


class BulkSkipForm(forms.Form):
    tasks = forms.ModelMultipleChoiceField(queryset=Task.objects.none(),
                                           widget=forms.CheckboxSelectMultiple)
    reason = forms.CharField(max_length=300)

    def __init__(self, *args, schedule, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tasks"].queryset = Task.objects.filter(
            schedule=schedule, status__in=UNFINISHED).order_by("due_date")
        self.fields["tasks"].label_from_instance = lambda t: f"{t.due_date:%d %b %Y}"


class ActivityForm(MoneyMixin):
    event_type = forms.ChoiceField(choices=[(c.value, c.label) for c in USER_EVENT_TYPES],
                                   label="Type")
    occurred_on = forms.DateField(widget=DateInput(), label="Date", initial=dt.date.today)
    occurred_time = forms.TimeField(required=False, widget=TimeInput(), label="Time (optional)")
    title = forms.CharField(max_length=200, required=False, label="Summary")
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), required=False,
                            help_text="Markdown is supported.")
    provider = forms.CharField(max_length=150, required=False)
    evidence = MultipleFileField(label="Attachments")
    create_task = forms.BooleanField(required=False,
                                     label="Create a follow-up task from this entry")


class CorrectionForm(MoneyMixin):
    reason = forms.CharField(max_length=300, label="Reason for correction")
    occurred_on = forms.DateField(widget=DateInput(), label="Date")
    title = forms.CharField(max_length=200, required=False, label="Summary")
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), required=False)
    provider = forms.CharField(max_length=150, required=False)


class ReminderForm(forms.Form):
    lead_days = forms.IntegerField(min_value=0, max_value=365, initial=7,
                                   label="Days before due date")
    time_of_day = forms.TimeField(widget=TimeInput(), initial=dt.time(9, 0), label="Time")
    recipient_mode = forms.ChoiceField(choices=RecipientMode.choices, label="Who")
    recipients = forms.ModelMultipleChoiceField(queryset=get_user_model().objects.none(),
                                                required=False,
                                                widget=forms.CheckboxSelectMultiple)
    channels = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple,
                                         help_text="Leave empty to use every enabled channel.")

    def __init__(self, *args, household, **kwargs):
        super().__init__(*args, **kwargs)
        from django.conf import settings

        self.fields["recipients"].queryset = active_member_users(household)
        self.fields["recipients"].label_from_instance = lambda u: u.label
        self.fields["channels"].choices = [(c, c.title()) for c in settings.NOTIFICATION_ADAPTERS]


class TaskFilterForm(forms.Form):
    status = forms.ChoiceField(required=False, choices=[
        ("open", "Open and in progress"), ("overdue", "Overdue"), ("soon", "Due in 14 days"),
        ("completed", "Completed"), ("skipped", "Skipped"), ("cancelled", "Cancelled"),
        ("all", "All")])
    assignee = forms.ChoiceField(required=False)
    q = forms.CharField(required=False, label="Search")

    def __init__(self, *args, household, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignee"].choices = [("", "Anyone"), ("me", "Assigned to me"),
                                           ("none", "Unassigned")] + [
            (str(u.pk), u.label) for u in active_member_users(household)]
