from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath, PureWindowsPath

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import FileResponse, Http404

from homefabric.core import audit
from homefabric.core.concurrency import versioned_update
from homefabric.core.errors import DomainError
from homefabric.documents import filetypes, storage
from homefabric.documents.models import (
    ActivityDocument,
    Document,
    DocumentRevision,
    EntityDocument,
    StoredBlob,
    TaskDocument,
)


def clean_filename(name: str) -> str:
    name = PureWindowsPath(PurePosixPath(name or "").name).name
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip().strip(".")
    return (name or "file")[:200]


def _store(household, uploaded_file):
    """Validate and store a file; return (blob, detected, sha256, size, filename)."""
    if uploaded_file is None:
        raise DomainError("Choose a file to upload.")
    limit = settings.MAX_UPLOAD_BYTES
    if uploaded_file.size is not None and uploaded_file.size > limit:
        raise DomainError(f"The file is larger than the {limit // (1024 * 1024)} MiB limit.")
    if uploaded_file.size == 0:
        raise DomainError("The file is empty.")
    filename = clean_filename(uploaded_file.name)
    staged = storage.stage_upload(uploaded_file)
    try:
        sha, size = storage.hash_file(staged)
        if size > limit:
            raise DomainError(f"The file is larger than the {limit // (1024 * 1024)} MiB limit.")
        try:
            detected = filetypes.detect(staged, filename)
        except filetypes.RejectedFile as exc:
            raise DomainError(str(exc)) from exc
        blob = StoredBlob.objects.filter(household=household, sha256=sha).first()
        if blob is not None and storage.path_for(blob.storage_key).exists():
            storage.discard(staged)
            staged = None
        else:
            key = blob.storage_key if blob else storage.new_storage_key(household.pk)
            storage.commit_blob(staged, key)
            staged = None
            if blob is None:
                blob = StoredBlob.objects.create(household=household, sha256=sha, size=size,
                                                 storage_key=key)
        return blob, detected, sha, size, filename
    finally:
        storage.discard(staged)


def _new_revision(document, uploaded_file, *, actor, note=""):
    blob, detected, sha, size, filename = _store(document.household, uploaded_file)
    number = (document.revisions.aggregate(m=Max("revision_number"))["m"] or 0) + 1
    return DocumentRevision.objects.create(
        document=document, revision_number=number, blob=blob, original_filename=filename,
        mime_type=detected.mime_type, size=size, sha256=sha, note=note[:300], uploaded_by=actor,
    )


def upload_document(household, uploaded_file, *, actor, display_name="", description="",
                    category="other", entity=None, task=None) -> Document:
    try:
        with transaction.atomic():
            document = Document.objects.create(
                household=household,
                display_name=(display_name or clean_filename(uploaded_file.name))[:200],
                description=description, category=category, created_by=actor,
            )
            revision = _new_revision(document, uploaded_file, actor=actor)
            document.current_revision = revision
            document.save(update_fields=["current_revision"])
            if entity is not None:
                attach_to_entity(document, entity, actor=actor)
            if task is not None:
                attach_to_task(document, task, actor=actor)
            audit.record(actor, "document.upload", document,
                         changes={"filename": revision.original_filename, "sha256": revision.sha256})
    except IntegrityError as exc:
        raise DomainError("The upload could not be saved; please try again.") from exc
    return document


def add_revision(document: Document, uploaded_file, *, actor, expected_version: int,
                 note: str = "") -> DocumentRevision:
    with transaction.atomic():
        revision = _new_revision(document, uploaded_file, actor=actor, note=note)
        versioned_update(document, expected_version, current_revision=revision)
        audit.record(actor, "document.revise", document,
                     changes={"revision": revision.revision_number, "sha256": revision.sha256})
    return revision


def update_metadata(document: Document, *, actor, expected_version: int, display_name: str,
                    description: str, category: str) -> None:
    data = {"display_name": display_name.strip()[:200], "description": description,
            "category": category}
    changes = audit.diff(document, data, data.keys())
    with transaction.atomic():
        versioned_update(document, expected_version, **data)
        audit.record(actor, "document.update", document, changes=changes)


def _same_household(a, b):
    if a.household_id != b.household_id:
        raise DomainError("Documents can only be linked within one household.")


def attach_to_entity(document: Document, entity, *, actor) -> EntityDocument:
    from homefabric.assets.services import ensure_editable

    _same_household(document, entity)
    ensure_editable(entity)
    link, created = EntityDocument.objects.get_or_create(
        household_id=entity.household_id, entity=entity, document=document,
        defaults={"attached_by": actor},
    )
    if created:
        audit.record(actor, "document.attach", document, changes={"entity": str(entity.pk)},
                     label=f"{document} → {entity}")
    return link


def detach_from_entity(link: EntityDocument, *, actor) -> None:
    from homefabric.assets.services import ensure_editable

    ensure_editable(link.entity)
    audit.record(actor, "document.detach", link.document,
                 changes={"entity": str(link.entity_id)}, label=f"{link.document} ✕ {link.entity}")
    link.delete()


def attach_to_task(document: Document, task, *, actor) -> TaskDocument:
    _same_household(document, task)
    link, _ = TaskDocument.objects.get_or_create(
        household_id=task.household_id, task=task, document=document,
        defaults={"attached_by": actor},
    )
    return link


def attach_revision_to_activity(revision: DocumentRevision, activity, *, actor):
    _same_household(revision.document, activity)
    link, _ = ActivityDocument.objects.get_or_create(
        household_id=activity.household_id, activity=activity, revision=revision,
        defaults={"attached_by": actor},
    )
    return link


def download_response(revision: DocumentRevision, *, inline: bool = False) -> FileResponse:
    path = storage.path_for(revision.blob.storage_key)
    if not path.exists():
        raise Http404("The stored file is missing. Run check_integrity.")
    as_inline = inline and revision.mime_type in filetypes.INLINE_SAFE
    response = FileResponse(
        open(path, "rb"),  # noqa: SIM115 - FileResponse closes it
        as_attachment=not as_inline,
        filename=revision.original_filename,
        content_type=revision.mime_type,
    )
    response["X-Content-Type-Options"] = "nosniff"
    # Browsers' built-in PDF viewers refuse to run in a sandboxed document, so
    # PDFs (verified by signature) get a restrictive policy without sandbox.
    if as_inline and revision.mime_type == "application/pdf":
        response["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'self'"
    else:
        response["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    response["Cache-Control"] = "private, no-store"
    if as_inline:
        response["X-Frame-Options"] = "SAMEORIGIN"
    return response
