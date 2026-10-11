from django.contrib import messages
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from homefabric.accounts.permissions import household_view, scoped
from homefabric.assets.models import Entity
from homefabric.core.errors import DomainError
from homefabric.core.pagination import paginate
from homefabric.documents import services
from homefabric.documents.forms import (
    AttachForm,
    DocumentFilterForm,
    MetadataForm,
    RevisionForm,
    UploadForm,
)
from homefabric.documents.models import (
    ActivityDocument,
    Document,
    DocumentRevision,
    EntityDocument,
    TaskDocument,
)


def _documents(request):
    return Document.objects.filter(household=request.household).select_related(
        "current_revision")


@household_view("view")
def document_list(request):
    form = DocumentFilterForm(request.GET or None)
    qs = _documents(request)
    if form.is_valid():
        q = form.cleaned_data.get("q")
        if q:
            qs = qs.filter(Q(display_name__icontains=q) | Q(description__icontains=q)
                           | Q(revisions__original_filename__icontains=q)).distinct()
        if form.cleaned_data.get("category"):
            qs = qs.filter(category=form.cleaned_data["category"])
    page = paginate(request, qs.order_by("display_name"))
    return render(request, "documents/document_list.html", {"form": form, "page": page})


@household_view("edit")
def document_upload(request):
    initial = {}
    entity = None
    if request.GET.get("entity"):
        entity = Entity.objects.filter(household=request.household,
                                       pk=request.GET["entity"]).first()
        initial["entity"] = entity
    if request.GET.get("category"):
        initial["category"] = request.GET["category"]
    form = UploadForm(request.POST or None, request.FILES or None, household=request.household,
                      initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            document = services.upload_document(
                request.household, d["file"], actor=request.user,
                display_name=d.get("display_name", ""), description=d.get("description", ""),
                category=d["category"], entity=d.get("entity"))
        except DomainError as exc:
            form.add_error("file", str(exc))
        else:
            messages.success(request, f"Uploaded '{document.display_name}'.")
            return redirect(d.get("entity") or document)
    return render(request, "documents/upload.html", {"form": form, "entity": entity})


@household_view("view")
def document_detail(request, pk):
    document = scoped(request, _documents(request).select_related("created_by"), pk=pk)
    revisions = document.revisions.select_related("uploaded_by")
    return render(request, "documents/document_detail.html", {
        "document": document,
        "revisions": revisions,
        "entity_links": EntityDocument.objects.filter(document=document).select_related(
            "entity", "entity__entity_type"),
        "task_links": TaskDocument.objects.filter(document=document).select_related("task"),
        "activity_links": ActivityDocument.objects.filter(
            revision__document=document).select_related("activity", "activity__entity",
                                                         "revision"),
    })


@household_view("edit")
def document_edit(request, pk):
    document = scoped(request, _documents(request), pk=pk)
    form = MetadataForm(request.POST or None, initial={
        "display_name": document.display_name, "category": document.category,
        "description": document.description, "version": document.version})
    if request.method == "POST" and form.is_valid():
        try:
            services.update_metadata(document, actor=request.user,
                                     expected_version=form.cleaned_data["version"],
                                     display_name=form.cleaned_data["display_name"],
                                     description=form.cleaned_data["description"],
                                     category=form.cleaned_data["category"])
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Document details saved.")
            return redirect(document)
    return render(request, "documents/document_edit.html", {"form": form, "document": document})


@household_view("edit")
def document_revise(request, pk):
    document = scoped(request, _documents(request), pk=pk)
    form = RevisionForm(request.POST or None, request.FILES or None,
                        initial={"version": document.version})
    if request.method == "POST" and form.is_valid():
        try:
            rev = services.add_revision(document, form.cleaned_data["file"], actor=request.user,
                                        expected_version=form.cleaned_data["version"],
                                        note=form.cleaned_data.get("note", ""))
        except DomainError as exc:
            form.add_error("file", str(exc))
        else:
            messages.success(request, f"Revision {rev.revision_number} uploaded. Earlier "
                             "revisions remain available.")
            return redirect(document)
    return render(request, "documents/document_revise.html", {"form": form, "document": document})


@household_view("view")
def revision_download(request, pk):
    revision = DocumentRevision.objects.select_related("document", "blob").filter(
        pk=pk, document__household=request.household).first()
    if revision is None:
        from django.http import Http404

        raise Http404
    return services.download_response(revision, inline=request.GET.get("inline") == "1")


@household_view("edit")
def attach_existing(request, entity_pk):
    entity = scoped(request, Entity.objects.all(), pk=entity_pk)
    form = AttachForm(request.POST or None, household=request.household)
    if request.method == "POST" and form.is_valid():
        try:
            services.attach_to_entity(form.cleaned_data["document"], entity, actor=request.user)
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Document attached.")
            return redirect(entity)
    return render(request, "documents/attach.html", {"form": form, "entity": entity})


@require_POST
@household_view("edit")
def detach(request, pk):
    link = scoped(request, EntityDocument.objects.select_related("entity", "document"), pk=pk)
    try:
        services.detach_from_entity(link, actor=request.user)
    except DomainError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Document unlinked. The document itself is kept.")
    return redirect(link.entity)
