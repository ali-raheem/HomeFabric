import datetime as dt

from django.db.models import Q
from django.shortcuts import redirect, render

from homefabric.accounts.permissions import can, household_view
from homefabric.assets.models import Entity, EntityStatus
from homefabric.core.dates import household_today
from homefabric.core.pagination import paginate
from homefabric.documents.models import Document
from homefabric.maintenance.models import UNFINISHED, ActivityEntry, Task


def home(request):
    if request.user.is_authenticated:
        return redirect("core:dashboard")
    return redirect("accounts:login")


@household_view("view")
def dashboard(request):
    from homefabric.notifications import services as notify
    from homefabric.notifications.models import NotificationEndpoint
    from homefabric.operations.jobs import scheduler_health

    hh = request.household
    today = household_today(hh)
    open_tasks = Task.objects.filter(household=hh, status__in=UNFINISHED).select_related(
        "entity", "assignee")
    soon_end = today + dt.timedelta(days=14)
    renewals = Entity.objects.filter(household=hh, status=EntityStatus.ACTIVE).filter(
        Q(renewal_date__gte=today, renewal_date__lte=today + dt.timedelta(days=60))
        | Q(end_date__gte=today, end_date__lte=today + dt.timedelta(days=60))
        | Q(warranty_end__gte=today, warranty_end__lte=today + dt.timedelta(days=60))
    ).select_related("entity_type")[:10]
    ctx = {
        "today": today,
        "overdue": open_tasks.filter(due_date__lt=today).order_by("due_date")[:10],
        "overdue_count": open_tasks.filter(due_date__lt=today).count(),
        "due_soon": open_tasks.filter(due_date__gte=today, due_date__lte=soon_end).order_by(
            "due_date")[:10],
        "mine": open_tasks.filter(assignee=request.user).order_by("due_date")[:10],
        "recent": ActivityEntry.objects.filter(household=hh, superseded=False).select_related(
            "entity", "author").order_by("-occurred_on", "-created_at")[:8],
        "renewals": renewals,
        "scheduler": scheduler_health(),
        "notify": notify.health_summary(hh),
        "my_failures": notify.failures_for_user(request.user)[:5],
        "household_failures": notify.household_failures(hh)[:5] if can(request, "manage") else [],
        "has_channels": NotificationEndpoint.objects.filter(user=request.user, deleted=False,
                                                            enabled=True).exists(),
        "entity_count": Entity.objects.filter(household=hh, status=EntityStatus.ACTIVE).count(),
    }
    return render(request, "core/dashboard.html", ctx)


SECTIONS = ("entities", "history", "documents", "tasks")


@household_view("view")
def search(request):
    q = (request.GET.get("q") or "").strip()
    section = request.GET.get("in")
    hh = request.household
    results = {}
    if len(q) >= 2:
        results["entities"] = Entity.objects.filter(household=hh).filter(
            Q(name__icontains=q) | Q(description__icontains=q) | Q(tags__name__icontains=q)
            | Q(model_number__icontains=q) | Q(serial_number__icontains=q)
            | Q(manufacturer__icontains=q) | Q(reference_number__icontains=q)
            | Q(provider__icontains=q)
        ).select_related("entity_type").distinct().order_by("status", "name")
        results["history"] = ActivityEntry.objects.filter(household=hh, superseded=False).filter(
            Q(title__icontains=q) | Q(notes__icontains=q) | Q(provider__icontains=q)
        ).select_related("entity").order_by("-occurred_on")
        results["documents"] = Document.objects.filter(household=hh).filter(
            Q(display_name__icontains=q) | Q(description__icontains=q)
            | Q(revisions__original_filename__icontains=q)
        ).distinct().order_by("display_name")
        results["tasks"] = Task.objects.filter(household=hh).filter(
            Q(title__icontains=q) | Q(description__icontains=q) | Q(result__icontains=q)
        ).select_related("entity").order_by("-due_date")
    pages = dict.fromkeys(SECTIONS)
    for name, qs in results.items():
        if section in SECTIONS and section != name:
            continue
        pages[name] = paginate(request, qs, per_page=25 if section == name else 8)
    return render(request, "core/search.html", {"q": q, "pages": pages, "section": section,
                                                "too_short": 0 < len(q) < 2})
