from django.contrib import messages
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from homefabric.accounts.permissions import household_view, scoped
from homefabric.assets import services
from homefabric.assets.forms import (
    ArchiveForm,
    EntityFilterForm,
    EntityForm,
    EntityTypeForm,
    MoveForm,
    RelationshipForm,
)
from homefabric.assets.models import Entity, EntityRelationship, RelationKind
from homefabric.core.dates import household_today
from homefabric.core.errors import DomainError
from homefabric.core.pagination import paginate


def _entities(request):
    return Entity.objects.filter(household=request.household).select_related("entity_type")


@household_view("view")
def entity_list(request):
    form = EntityFilterForm(request.GET or None)
    qs = _entities(request).prefetch_related("tags")
    status = "active"
    if form.is_valid():
        d = form.cleaned_data
        status = d.get("status") or "active"
        if d.get("q"):
            qs = qs.filter(Q(name__icontains=d["q"]) | Q(model_number__icontains=d["q"])
                           | Q(serial_number__icontains=d["q"]))
        if d.get("kind"):
            qs = qs.filter(entity_type__base_kind=d["kind"])
        if d.get("tag"):
            qs = qs.filter(tags__name__iexact=d["tag"])
    if status != "all":
        qs = qs.filter(status=status)
    page = paginate(request, qs.order_by("name").distinct())
    return render(request, "assets/entity_list.html", {"form": form, "page": page})


@household_view("edit")
def entity_create(request):
    initial = {}
    if request.GET.get("location"):
        initial["location"] = _entities(request).filter(pk=request.GET["location"]).first()
    if request.GET.get("kind"):
        initial["entity_type"] = services.types_for(request.household).filter(
            base_kind=request.GET["kind"], household__isnull=True).first()
    form = EntityForm(request.POST or None, household=request.household, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            entity = services.create_entity(
                request.household, actor=request.user, data=form.entity_data(),
                tags=services.split_tags(form.cleaned_data.get("tags")),
                location=form.cleaned_data.get("location"),
            )
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            parent = request.GET.get("part_of")
            if parent:
                target = _entities(request).filter(pk=parent).first()
                if target:
                    try:
                        services.add_relationship(entity, target, RelationKind.PART_OF,
                                                  actor=request.user)
                    except DomainError as exc:
                        messages.warning(request, str(exc))
            messages.success(request, f"Created '{entity.name}'.")
            return redirect(entity)
    return render(request, "assets/entity_form.html", {"form": form, "creating": True})


@household_view("view")
def entity_detail(request, pk):
    from homefabric.documents.models import EntityDocument
    from homefabric.maintenance.models import (
        UNFINISHED,
        ActivityEntry,
        MaintenanceSchedule,
        Task,
    )

    entity = scoped(request, Entity.objects.select_related("entity_type", "created_by",
                                                           "updated_by"), pk=pk)
    today = household_today(request.household)
    rels = services.relationships_for(entity)
    contents = services.contents(entity)
    history = ActivityEntry.objects.filter(entity=entity, superseded=False).select_related(
        "author", "task").prefetch_related("document_links__revision__document")
    ctx = {
        "entity": entity,
        "today": today,
        "path": services.location_path(entity),
        "rels": rels,
        "contents_page": paginate(request, contents, per_page=20, param="cpage"),
        "components": services.components(entity)[:50],
        "open_tasks": Task.objects.filter(entity=entity, status__in=UNFINISHED).select_related(
            "assignee", "schedule").order_by("due_date")[:50],
        "schedules": MaintenanceSchedule.objects.filter(entity=entity).order_by("-enabled", "title"),
        "documents": EntityDocument.objects.filter(entity=entity).select_related(
            "document", "document__current_revision").order_by("document__display_name"),
        "history_page": paginate(request, history, per_page=15, param="hpage"),
        "latest_work": history.filter(event_type__in=["service", "repair", "inspection", "work",
                                                      "installation"]).first(),
        "outstanding": services.outstanding_work(entity),
    }
    return render(request, "assets/entity_detail.html", ctx)


@household_view("edit")
def entity_edit(request, pk):
    entity = scoped(request, _entities(request), pk=pk)
    if entity.is_archived:
        messages.error(request, "Archived items are read-only. Restore it to make changes.")
        return redirect(entity)
    form = EntityForm(request.POST or None, household=request.household, entity=entity,
                      initial=EntityForm.initial_for(entity))
    if request.method == "POST" and form.is_valid():
        try:
            services.update_entity(
                entity, actor=request.user, expected_version=form.cleaned_data["version"],
                data=form.entity_data(), tags=services.split_tags(form.cleaned_data.get("tags")))
            new_location = form.cleaned_data.get("location")
            if new_location != services.location_of(entity):
                services.set_location(entity, new_location, actor=request.user)
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Changes saved.")
            return redirect(entity)
    return render(request, "assets/entity_form.html", {"form": form, "entity": entity})


@household_view("edit")
def entity_move(request, pk):
    entity = scoped(request, _entities(request), pk=pk)
    form = MoveForm(request.POST or None, entity=entity,
                    initial={"location": services.location_of(entity), "version": entity.version})
    if request.method == "POST" and form.is_valid():
        try:
            services.set_location(entity, form.cleaned_data["location"], actor=request.user)
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Location updated.")
            return redirect(entity)
    return render(request, "assets/entity_move.html", {"form": form, "entity": entity})


@household_view("edit")
def entity_archive(request, pk):
    entity = scoped(request, _entities(request), pk=pk)
    work = services.outstanding_work(entity)
    has_work = work["tasks"].exists() or work["schedules"].exists()
    form = ArchiveForm(request.POST or None, initial={"version": entity.version})
    if has_work:
        form.fields["outstanding"].required = True
    if request.method == "POST" and form.is_valid():
        try:
            services.archive_entity(entity, actor=request.user,
                                    expected_version=form.cleaned_data["version"],
                                    outstanding=form.cleaned_data.get("outstanding") or None,
                                    reason=form.cleaned_data.get("reason", ""))
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"'{entity.name}' archived. Its history remains available.")
            return redirect(entity)
    return render(request, "assets/entity_archive.html",
                  {"form": form, "entity": entity, "work": work, "has_work": has_work,
                   "today": household_today(request.household)})


@require_POST
@household_view("edit")
def entity_restore(request, pk):
    entity = scoped(request, _entities(request), pk=pk)
    try:
        services.restore_entity(entity, actor=request.user,
                                expected_version=int(request.POST.get("version", 0)))
    except DomainError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"'{entity.name}' restored.")
    return redirect(entity)


@household_view("edit")
def relationship_add(request, pk):
    entity = scoped(request, _entities(request), pk=pk)
    form = RelationshipForm(request.POST or None, entity=entity,
                            initial={"kind": request.GET.get("kind", RelationKind.PART_OF)})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        source, target = (entity, d["other"]) if d["direction"] == "out" else (d["other"], entity)
        try:
            services.add_relationship(source, target, d["kind"], actor=request.user,
                                      label=d.get("label", ""))
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Relationship added.")
            return redirect(entity)
    return render(request, "assets/relationship_form.html", {"form": form, "entity": entity})


@require_POST
@household_view("edit")
def relationship_remove(request, pk):
    rel = scoped(request, EntityRelationship.objects.select_related("source", "target"), pk=pk)
    back = request.POST.get("next_entity")
    try:
        services.remove_relationship(rel, actor=request.user)
    except DomainError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Relationship removed.")
    entity = _entities(request).filter(pk=back).first() if back else None
    return redirect(entity or rel.source)


@household_view("view")
def explorer(request):
    mode = request.GET.get("mode", "location")
    if mode == "system":
        roots = services.top_level_systems(request.household)
        counts = services.child_count_map([e.pk for e in roots], RelationKind.PART_OF)
        unplaced = None
    else:
        mode = "location"
        roots = services.top_level_locations(request.household)
        counts = services.child_count_map([e.pk for e in roots], RelationKind.LOCATED_IN)
        unplaced = services.unplaced_entities(request.household)
    page = paginate(request, roots, per_page=50)
    nodes = [(e, counts.get(e.pk, 0)) for e in page]
    return render(request, "assets/explorer.html", {
        "mode": mode, "page": page, "nodes": nodes,
        "unplaced_page": paginate(request, unplaced, per_page=20, param="upage")
        if unplaced is not None else None,
    })


@household_view("view")
def explorer_children(request, pk):
    entity = scoped(request, _entities(request), pk=pk)
    mode = request.GET.get("mode", "location")
    if mode == "system":
        children = services.components(entity)
        kind = RelationKind.PART_OF
    else:
        children = services.contents(entity)
        kind = RelationKind.LOCATED_IN
    page = paginate(request, children, per_page=50)
    counts = services.child_count_map([e.pk for e in page], kind)
    nodes = [(e, counts.get(e.pk, 0)) for e in page]
    depth = int(request.GET.get("depth", 1))
    return render(request, "assets/_tree_children.html", {
        "parent": entity, "nodes": nodes, "page": page, "mode": mode, "depth": depth,
    })


@household_view("view")
def type_list(request):
    types = services.types_for(request.household)
    form = EntityTypeForm(request.POST or None)
    if request.method == "POST":
        from homefabric.accounts.permissions import require

        require(request, "manage")
        if form.is_valid():
            try:
                services.create_entity_type(request.household, actor=request.user,
                                            **form.cleaned_data)
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "Type added.")
                return redirect("assets:type_list")
    return render(request, "assets/type_list.html", {"types": types, "form": form})
