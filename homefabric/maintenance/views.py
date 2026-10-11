import datetime as dt

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from homefabric.accounts.permissions import household_view, scoped
from homefabric.assets.models import Entity
from homefabric.core.dates import household_today
from homefabric.core.errors import DomainError
from homefabric.core.pagination import paginate
from homefabric.documents.models import ActivityDocument, TaskDocument
from homefabric.maintenance import services
from homefabric.maintenance.forms import (
    ActivityForm,
    BulkSkipForm,
    CloseForm,
    CompleteForm,
    CorrectionForm,
    EnableScheduleForm,
    FollowUpForm,
    ReminderForm,
    ScheduleForm,
    TaskFilterForm,
    TaskForm,
)
from homefabric.maintenance.models import (
    UNFINISHED,
    ActivityEntry,
    MaintenanceSchedule,
    RecurrenceMode,
    ReminderRule,
    Task,
    TaskStatus,
)


def _tasks(request):
    return Task.objects.filter(household=request.household).select_related(
        "entity", "entity__entity_type", "assignee", "schedule")


def _version(request):
    try:
        return int(request.POST.get("version", 0))
    except ValueError:
        return 0


# --- tasks ----------------------------------------------------------------

@household_view("view")
def task_list(request):
    today = household_today(request.household)
    form = TaskFilterForm(request.GET or None, household=request.household)
    qs = _tasks(request)
    status = request.GET.get("status") or "open"
    if status == "open":
        qs = qs.filter(status__in=UNFINISHED)
    elif status == "overdue":
        qs = qs.filter(status__in=UNFINISHED, due_date__lt=today)
    elif status == "soon":
        qs = qs.filter(status__in=UNFINISHED, due_date__gte=today,
                       due_date__lte=today + dt.timedelta(days=14))
    elif status in TaskStatus.values:
        qs = qs.filter(status=status)
    assignee = request.GET.get("assignee")
    if assignee == "me":
        qs = qs.filter(assignee=request.user)
    elif assignee == "none":
        qs = qs.filter(assignee__isnull=True)
    elif assignee and assignee.isdigit():
        qs = qs.filter(assignee_id=int(assignee))
    if request.GET.get("q"):
        q = request.GET["q"]
        qs = qs.filter(Q(title__icontains=q) | Q(entity__name__icontains=q))
    if request.GET.get("entity"):
        qs = qs.filter(entity_id=request.GET["entity"])
    order = ["-completed_on", "-updated_at"] if status in ("completed", "skipped", "cancelled") \
        else ["due_date", "title"]
    page = paginate(request, qs.order_by(*order))
    return render(request, "maintenance/task_list.html",
                  {"form": form, "page": page, "today": today, "status": status})


@household_view("edit")
def task_create(request):
    initial = {}
    entity_id = request.GET.get("entity")
    if entity_id:
        initial["entity"] = Entity.objects.filter(household=request.household,
                                                  pk=entity_id).first()
    source = None
    if request.GET.get("from_activity"):
        source = ActivityEntry.objects.filter(household=request.household,
                                              pk=request.GET["from_activity"]).first()
        if source:
            initial.update(entity=source.entity,
                           title=f"Follow up: {source.headline}"[:200],
                           description=source.notes)
    if request.GET.get("related"):
        related = _tasks(request).filter(pk=request.GET["related"]).first()
        if related:
            initial.update(entity=related.entity, related_task=related, kind="booking",
                           title=f"Book: {related.title}"[:200])
    form = TaskForm(request.POST or None, household=request.household, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            task = services.create_task(
                d["entity"], actor=request.user, title=d["title"],
                description=d.get("description", ""), due_date=d.get("due_date"),
                assignee=d.get("assignee"), kind=d["kind"], related_task=d.get("related_task"),
                reminders=d.get("reminders", False), source_activity=source)
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Task '{task.title}' created.")
            return redirect(task)
    return render(request, "maintenance/task_form.html", {"form": form, "creating": True,
                                                          "source": source})


@household_view("view")
def task_detail(request, pk):
    task = scoped(request, _tasks(request).select_related("related_task", "completed_by",
                                                          "created_by", "source_activity"), pk=pk)
    today = household_today(request.household)
    return render(request, "maintenance/task_detail.html", {
        "task": task, "today": today,
        "activity": ActivityEntry.objects.filter(task=task).select_related("author")
        .prefetch_related("document_links__revision__document"),
        "documents": TaskDocument.objects.filter(task=task).select_related(
            "document", "document__current_revision"),
        "related_from": _tasks(request).filter(related_task=task),
        "task_rules": task.reminder_rules.all(),
        "schedule_rules": task.schedule.reminder_rules.all() if task.schedule_id else [],
    })


@household_view("edit")
def task_edit(request, pk):
    task = scoped(request, _tasks(request), pk=pk)
    if not task.is_unfinished:
        messages.error(request, "Finished tasks cannot be edited. Create a follow-up or correct "
                       "the history entry.")
        return redirect(task)
    form = TaskForm(request.POST or None, household=request.household, editing=True, initial={
        "title": task.title, "description": task.description, "due_date": task.due_date,
        "assignee": task.assignee, "related_task": task.related_task,
        "reminders": task.reminders_enabled, "version": task.version})
    form.fields["related_task"].queryset = form.fields["related_task"].queryset.exclude(pk=task.pk)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            services.update_task(task, actor=request.user, expected_version=d["version"],
                                 title=d["title"], description=d.get("description", ""),
                                 due_date=d.get("due_date"), assignee=d.get("assignee"),
                                 related_task=d.get("related_task"),
                                 reminders_enabled=d.get("reminders", False))
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Task updated.")
            return redirect(task)
    return render(request, "maintenance/task_form.html", {"form": form, "task": task})


@require_POST
@household_view("edit")
def task_start(request, pk):
    task = scoped(request, _tasks(request), pk=pk)
    try:
        services.start_task(task, actor=request.user, expected_version=_version(request))
    except DomainError as exc:
        messages.error(request, str(exc))
    return redirect(task)


@household_view("edit")
def task_complete(request, pk):
    task = scoped(request, _tasks(request), pk=pk)
    if not task.is_unfinished:
        return redirect(task)
    form = CompleteForm(request.POST or None, request.FILES or None, task=task, initial={
        "completed_on": household_today(request.household), "version": task.version,
        "currency": request.household.currency,
        "event_type": "booking" if task.kind == "booking" else (
            "service" if task.schedule_id else "work")})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            services.complete_task(
                task, actor=request.user, expected_version=d["version"],
                completed_on=d["completed_on"], event_type=d["event_type"],
                notes=d.get("notes", ""), provider=d.get("provider", ""), amount=d.get("amount"),
                currency=d.get("currency") or request.household.currency,
                uploads=d.get("evidence") or [])
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            task.refresh_from_db()
            msg = "Task completed and recorded in the item's history."
            if task.schedule_id and task.schedule.mode == RecurrenceMode.AFTER_COMPLETION:
                nxt = Task.objects.filter(schedule=task.schedule, status__in=UNFINISHED).first()
                if nxt:
                    msg += f" Next due {nxt.due_date:%d %b %Y}."
            messages.success(request, msg)
            return redirect(task)
    return render(request, "maintenance/task_complete.html", {"form": form, "task": task})


def _close_view(request, pk, action):
    task = scoped(request, _tasks(request), pk=pk)
    if not task.is_unfinished:
        return redirect(task)
    form = CloseForm(request.POST or None, initial={"version": task.version})
    needs_next = bool(task.schedule_id and task.schedule.mode == RecurrenceMode.AFTER_COMPLETION
                      and task.schedule.enabled)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        fn = services.skip_task if action == "skip" else services.cancel_task
        try:
            fn(task, actor=request.user, expected_version=d["version"], reason=d["reason"],
               new_due_date=d.get("new_due_date"), pause_schedule=d.get("pause_schedule", False))
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Task {'skipped' if action == 'skip' else 'cancelled'}.")
            return redirect(task)
    return render(request, "maintenance/task_close.html",
                  {"form": form, "task": task, "action": action, "needs_next": needs_next})


@household_view("edit")
def task_skip(request, pk):
    return _close_view(request, pk, "skip")


@household_view("edit")
def task_cancel(request, pk):
    return _close_view(request, pk, "cancel")


@household_view("edit")
def task_follow_up(request, pk):
    task = scoped(request, _tasks(request), pk=pk)
    form = FollowUpForm(request.POST or None, initial={"title": f"Follow up: {task.title}"[:200]})
    if request.method == "POST" and form.is_valid():
        try:
            new = services.create_follow_up(task, actor=request.user, **form.cleaned_data)
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Follow-up task created.")
            return redirect(new)
    return render(request, "maintenance/task_follow_up.html", {"form": form, "task": task})


# --- reminders ------------------------------------------------------------

@household_view("edit")
def reminder_add(request, owner, pk):
    if owner == "task":
        target = scoped(request, _tasks(request), pk=pk)
    else:
        target = scoped(request, MaintenanceSchedule.objects.all(), pk=pk)
    form = ReminderForm(request.POST or None, household=request.household)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        rule = ReminderRule.objects.create(
            household=request.household, task=target if owner == "task" else None,
            schedule=target if owner == "schedule" else None, lead_days=d["lead_days"],
            time_of_day=d["time_of_day"], recipient_mode=d["recipient_mode"],
            channels=d.get("channels") or [])
        rule.recipients.set(d.get("recipients") or [])
        if owner == "task" and not target.reminders_enabled:
            Task.objects.filter(pk=target.pk).update(reminders_enabled=True)
        messages.success(request, "Reminder added.")
        return redirect(target)
    return render(request, "maintenance/reminder_form.html", {"form": form, "target": target})


@require_POST
@household_view("edit")
def reminder_delete(request, pk):
    rule = scoped(request, ReminderRule.objects.select_related("task", "schedule"), pk=pk)
    target = rule.task or rule.schedule
    rule.delete()
    messages.success(request, "Reminder removed.")
    return redirect(target)


# --- schedules ------------------------------------------------------------

def _schedules(request):
    return MaintenanceSchedule.objects.filter(household=request.household).select_related(
        "entity", "default_assignee")


@household_view("view")
def schedule_list(request):
    qs = _schedules(request).order_by("-enabled", "title")
    if request.GET.get("entity"):
        qs = qs.filter(entity_id=request.GET["entity"])
    page = paginate(request, qs)
    next_due = {}
    for t in Task.objects.filter(schedule__in=list(page), status__in=UNFINISHED).order_by(
            "due_date"):
        next_due.setdefault(t.schedule_id, t.due_date)
    rows = [(s, next_due.get(s.pk)) for s in page]
    return render(request, "maintenance/schedule_list.html", {"page": page, "rows": rows})


@household_view("edit")
def schedule_create(request):
    entity = None
    if request.GET.get("entity"):
        entity = Entity.objects.filter(household=request.household,
                                       pk=request.GET["entity"]).first()
    if entity is None:
        messages.info(request, "Open an item and choose 'Add schedule'.")
        return redirect("assets:entity_list")
    form = ScheduleForm(request.POST or None, household=request.household)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            schedule = services.create_schedule(
                entity, actor=request.user, title=d["title"], mode=d["mode"],
                interval_count=d["interval_count"], interval_unit=d["interval_unit"],
                anchor_date=d["anchor_date"], description=d.get("description", ""),
                default_assignee=d.get("default_assignee"), horizon_days=d.get("horizon_days"),
                reminders=d.get("reminders", False))
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Schedule created. {schedule.tasks.count()} occurrence(s) "
                             "generated.")
            return redirect(schedule)
    return render(request, "maintenance/schedule_form.html", {"form": form, "entity": entity,
                                                              "creating": True})


@household_view("view")
def schedule_preview(request):
    """HTMX partial: upcoming dates for the recurrence fields in the form."""
    dates, error = [], ""
    try:
        anchor = dt.date.fromisoformat(request.GET.get("anchor_date", ""))
        count = int(request.GET.get("interval_count", 1))
        unit = request.GET.get("interval_unit", "year")
        mode = request.GET.get("mode", RecurrenceMode.FIXED)
        if count < 1 or count > 1000 or unit not in ("day", "week", "month", "year"):
            raise ValueError
        dates = services.preview_dates(mode=mode, anchor_date=anchor, interval_count=count,
                                       interval_unit=unit, count=6)
    except (ValueError, TypeError):
        error = "Enter a first due date and interval to preview."
    return render(request, "maintenance/_recurrence_preview.html",
                  {"dates": dates, "error": error, "mode": request.GET.get("mode")})


@household_view("view")
def schedule_detail(request, pk):
    schedule = scoped(request, _schedules(request), pk=pk)
    today = household_today(request.household)
    tasks = Task.objects.filter(schedule=schedule).select_related("assignee")
    upcoming = tasks.filter(status__in=UNFINISHED).order_by("due_date")
    history = tasks.exclude(status__in=UNFINISHED).order_by("-due_date")
    bulk = BulkSkipForm(schedule=schedule) if schedule.mode == RecurrenceMode.FIXED else None
    return render(request, "maintenance/schedule_detail.html", {
        "schedule": schedule, "today": today, "upcoming": upcoming[:100],
        "history_page": paginate(request, history, per_page=20),
        "bulk_form": bulk, "overdue_count": upcoming.filter(due_date__lt=today).count(),
        "rules": schedule.reminder_rules.all(),
        "preview": services.preview_dates(
            mode=schedule.mode, anchor_date=schedule.anchor_date,
            interval_count=schedule.interval_count, interval_unit=schedule.interval_unit,
            from_date=today, count=4),
    })


@household_view("edit")
def schedule_edit(request, pk):
    schedule = scoped(request, _schedules(request), pk=pk)
    form = ScheduleForm(request.POST or None, household=request.household, editing=True, initial={
        "title": schedule.title, "mode": schedule.mode, "interval_count": schedule.interval_count,
        "interval_unit": schedule.interval_unit, "anchor_date": schedule.anchor_date,
        "default_assignee": schedule.default_assignee, "horizon_days": schedule.horizon_days,
        "description": schedule.description, "version": schedule.version})
    preview = None
    if request.method == "POST" and form.is_valid():
        d = {k: v for k, v in form.cleaned_data.items() if k != "version"}
        if "confirm" in request.POST:
            try:
                result = services.update_schedule(schedule, actor=request.user,
                                                  expected_version=form.cleaned_data["version"],
                                                  data=d)
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f"Schedule updated: {len(result['removed'])} future "
                                 f"occurrence(s) replaced, {len(result['created'])} generated. "
                                 "Past and started occurrences were preserved.")
                return redirect(schedule)
        else:
            preview = services.preview_schedule_change(schedule, data=d)
    return render(request, "maintenance/schedule_form.html",
                  {"form": form, "schedule": schedule, "entity": schedule.entity,
                   "change_preview": preview})


@household_view("edit")
def schedule_toggle(request, pk):
    schedule = scoped(request, _schedules(request), pk=pk)
    if schedule.enabled:
        if request.method == "POST":
            services.set_schedule_enabled(schedule, False, actor=request.user,
                                          allow_archived=True)
            messages.success(request, "Schedule disabled. Existing tasks were kept; no new "
                             "occurrences or reminders will be produced.")
            return redirect(schedule)
        return render(request, "maintenance/schedule_disable.html", {"schedule": schedule})
    preview = services.catch_up_preview(schedule)
    form = EnableScheduleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            created = services.set_schedule_enabled(
                schedule, True, actor=request.user, skip_missed=form.cleaned_data["skip_missed"],
                next_due_date=form.cleaned_data.get("next_due_date"))
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Schedule enabled; {len(created)} occurrence(s) generated.")
            return redirect(schedule)
    return render(request, "maintenance/schedule_enable.html",
                  {"schedule": schedule, "preview": preview, "form": form})


@require_POST
@household_view("edit")
def schedule_bulk_skip(request, pk):
    schedule = scoped(request, _schedules(request), pk=pk)
    form = BulkSkipForm(request.POST, schedule=schedule)
    if form.is_valid():
        try:
            n = services.bulk_skip(form.cleaned_data["tasks"], actor=request.user,
                                   reason=form.cleaned_data["reason"])
        except DomainError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Skipped {n} occurrence(s).")
    else:
        messages.error(request, "Select occurrences and give a reason to skip them.")
    return redirect(schedule)


# --- history --------------------------------------------------------------

@household_view("edit")
def activity_create(request, entity_pk):
    from homefabric.documents import services as docs

    entity = scoped(request, Entity.objects.select_related("entity_type"), pk=entity_pk)
    form = ActivityForm(request.POST or None, request.FILES or None, initial={
        "occurred_on": household_today(request.household), "currency": request.household.currency,
        "event_type": request.GET.get("type", "note")})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            from django.db import transaction

            with transaction.atomic():
                entry = services.add_activity(
                    entity, actor=request.user, event_type=d["event_type"],
                    occurred_on=d["occurred_on"], occurred_time=d.get("occurred_time"),
                    title=d.get("title", ""), notes=d.get("notes", ""),
                    provider=d.get("provider", ""), amount=d.get("amount"),
                    currency=d.get("currency") or request.household.currency)
                for upload in d.get("evidence") or []:
                    doc = docs.upload_document(request.household, upload, actor=request.user,
                                               display_name=f"{entity.name} – {upload.name}",
                                               category="other")
                    docs.attach_revision_to_activity(doc.current_revision, entry,
                                                     actor=request.user)
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "History entry added.")
            if d.get("create_task"):
                return redirect(reverse("maintenance:task_create")
                                + f"?from_activity={entry.pk}")
            return redirect(entity)
    return render(request, "maintenance/activity_form.html", {"form": form, "entity": entity})


@household_view("view")
def activity_detail(request, pk):
    entry = scoped(request, ActivityEntry.objects.select_related("entity", "author", "task"),
                   pk=pk)
    chain = services.correction_chain(entry)
    newer = ActivityEntry.objects.filter(correction_of=entry).first()
    return render(request, "maintenance/activity_detail.html", {
        "entry": entry, "chain": chain, "newer": newer,
        "documents": ActivityDocument.objects.filter(activity=entry).select_related(
            "revision", "revision__document"),
        "follow_ups": Task.objects.filter(source_activity=entry),
    })


@household_view("edit")
def activity_correct(request, pk):
    from homefabric.core import money

    entry = scoped(request, ActivityEntry.objects.select_related("entity"), pk=pk)
    if entry.superseded:
        newest = entry
        while newest.superseded:
            newest = ActivityEntry.objects.get(correction_of=newest)
        messages.info(request, "That entry was already corrected; this is the newest version.")
        return redirect("maintenance:activity_correct", pk=newest.pk)
    initial = {"occurred_on": entry.occurred_on, "title": entry.title, "notes": entry.notes,
               "provider": entry.provider, "currency": entry.currency or request.household.currency}
    if entry.amount_minor is not None:
        initial["amount"] = money.from_minor(entry.amount_minor, entry.currency)
    form = CorrectionForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            new = services.correct_activity(
                entry, actor=request.user, reason=d["reason"], occurred_on=d["occurred_on"],
                title=d.get("title", ""), notes=d.get("notes", ""),
                provider=d.get("provider", ""), amount=d.get("amount"),
                currency=d.get("currency") or request.household.currency)
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Correction saved. The original entry is preserved.")
            return redirect("maintenance:activity_detail", pk=new.pk)
    return render(request, "maintenance/activity_correct.html", {"form": form, "entry": entry})
