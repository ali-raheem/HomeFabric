"""Documents: A03, A10 (revisions), upload safety, private storage (spec §11)."""

import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import override_settings
from django.urls import reverse

from homefabric.core.errors import DomainError
from homefabric.documents import services, storage
from homefabric.documents.models import Document, DocumentRevision, StoredBlob
from homefabric.maintenance import services as maint
from homefabric.operations.integrity import check
from tests.conftest import PDF_BYTES, PNG_BYTES, docx, login, pdf

pytestmark = pytest.mark.django_db


def test_a03_manuals_independently_retrievable(home, owner, client):
    m1 = services.upload_document(home["boiler"].household, pdf("boiler.pdf", PDF_BYTES + b"1"),
                                  actor=owner, entity=home["boiler"], category="manual")
    m2 = services.upload_document(home["boiler"].household, pdf("filter.pdf", PDF_BYTES + b"2"),
                                  actor=owner, entity=home["filter"], category="manual")
    login(client, owner)
    r1 = client.get(reverse("documents:download", args=[m1.current_revision.pk]))
    r2 = client.get(reverse("documents:download", args=[m2.current_revision.pk]))
    assert b"".join(r1.streaming_content).endswith(b"1")
    assert b"".join(r2.streaming_content).endswith(b"2")
    assert "attachment" in r1["Content-Disposition"] and r1["X-Content-Type-Options"] == "nosniff"
    assert list(home["boiler"].document_links.values_list("document", flat=True)) == [m1.pk]


def test_a10_revisions_keep_older_invoice_pinned(home, owner):
    hh = home["boiler"].household
    doc = services.upload_document(hh, pdf("invoice-v1.pdf", PDF_BYTES + b"v1"), actor=owner)
    entry = maint.add_activity(home["boiler"], actor=owner, event_type="service",
                               occurred_on=__import__("datetime").date.today())
    services.attach_revision_to_activity(doc.current_revision, entry, actor=owner)
    rev2 = services.add_revision(doc, pdf("invoice-v2.pdf", PDF_BYTES + b"v2"), actor=owner,
                                 expected_version=doc.version, note="Corrected")
    doc.refresh_from_db()
    assert doc.current_revision == rev2 and doc.revisions.count() == 2
    pinned = entry.document_links.get().revision
    assert pinned.revision_number == 1 and pinned.original_filename == "invoice-v1.pdf"
    assert pinned.uploaded_by == owner


@pytest.mark.parametrize("name,content,msg", [
    ("evil.exe", b"MZ\x90\x00binary", "not allowed"),
    ("page.html", b"<html><script>alert(1)</script></html>", "not allowed"),
    ("pic.svg", b"<svg onload=alert(1)>", "not allowed"),
    ("notes.txt", b"<!DOCTYPE html><html>", "not supported"),
    ("script.pdf", b"#!/bin/sh\nrm -rf /", "Executable"),
    ("fake.pdf", b"MZ fake", "Executable"),
    ("archive.zip", b"PK\x03\x04junk", "corrupt"),
])
def test_rejects_executable_and_active_content(household, owner, name, content, msg):
    with pytest.raises(DomainError, match=msg):
        services.upload_document(household, SimpleUploadedFile(name, content), actor=owner)
    assert not Document.objects.exists()


def test_rejects_macro_office_files(household, owner):
    with pytest.raises(DomainError, match="macros"):
        services.upload_document(household, docx(macro=True), actor=owner)
    doc = services.upload_document(household, docx(), actor=owner)
    assert doc.current_revision.mime_type.endswith("wordprocessingml.document")


def test_accepts_common_formats(household, owner):
    for f in (SimpleUploadedFile("photo.png", PNG_BYTES), SimpleUploadedFile("a.txt", b"hello"),
              SimpleUploadedFile("data.csv", b"a,b\n1,2\n"), pdf()):
        services.upload_document(household, f, actor=owner)
    assert Document.objects.count() == 4


@override_settings(MAX_UPLOAD_BYTES=100)
def test_size_limit(household, owner):
    with pytest.raises(DomainError, match="limit"):
        services.upload_document(household, pdf(content=PDF_BYTES + b"x" * 200), actor=owner)


def test_path_traversal_and_opaque_names(household, owner):
    doc = services.upload_document(household, pdf("../../etc/passwd.pdf"), actor=owner)
    rev = doc.current_revision
    assert rev.original_filename == "passwd.pdf"
    path = storage.path_for(rev.blob.storage_key)
    assert "passwd" not in str(path) and storage.blob_root().resolve() in path.parents
    with pytest.raises(ValueError):
        storage.path_for("../../x/y")


def test_dedup_within_household_never_across(household, other_household, owner, other_owner):
    a = services.upload_document(household, pdf("a.pdf"), actor=owner)
    b = services.upload_document(household, pdf("b.pdf"), actor=owner)
    c = services.upload_document(other_household, pdf("c.pdf"), actor=other_owner)
    assert a.current_revision.blob == b.current_revision.blob
    assert c.current_revision.blob != a.current_revision.blob
    assert StoredBlob.objects.count() == 2
    assert a.current_revision.sha256 == c.current_revision.sha256


def test_failed_upload_does_not_appear_and_orphan_reported(household, owner, monkeypatch):
    def boom(*a, **k):
        raise IntegrityError("simulated database failure")

    monkeypatch.setattr(DocumentRevision.objects, "create", boom)
    with pytest.raises(DomainError):
        services.upload_document(household, pdf("x.pdf", PDF_BYTES + b"orphan"), actor=owner)
    assert not Document.objects.exists() and not StoredBlob.objects.exists()
    report = check()
    assert any(p["kind"] == "orphaned_file" for p in report["problems"])
    cleaned = check(clean=True)
    assert cleaned["cleaned"]
    assert check()["ok"]


def test_integrity_reports_missing_file(household, owner):
    doc = services.upload_document(household, pdf(), actor=owner)
    storage.path_for(doc.current_revision.blob.storage_key).unlink()
    assert any(p["kind"] == "missing_file" for p in check()["problems"])


def test_inline_view_only_for_safe_types(household, owner, client):
    doc = services.upload_document(household, pdf(), actor=owner)
    txt = services.upload_document(household, SimpleUploadedFile("a.txt", b"hi"), actor=owner)
    login(client, owner)
    r = client.get(reverse("documents:download", args=[doc.current_revision.pk]) + "?inline=1")
    assert r["Content-Disposition"].startswith("inline")
    r = client.get(reverse("documents:download", args=[txt.current_revision.pk]) + "?inline=1")
    assert r["Content-Disposition"].startswith("attachment")
    assert "sandbox" in r["Content-Security-Policy"]


def test_upload_via_web(home, owner, client):
    login(client, owner)
    r = client.post(reverse("documents:upload"), {
        "file": pdf("manual.pdf"), "category": "manual", "entity": home["boiler"].pk,
        "display_name": "Boiler manual"})
    assert r.status_code == 302
    assert home["boiler"].document_links.get().document.display_name == "Boiler manual"


def test_document_zip_bomb_like_office_rejected(household, owner):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("random.txt", "x")
    with pytest.raises(DomainError):
        services.upload_document(household, SimpleUploadedFile("x.docx", buf.getvalue()),
                                 actor=owner)
