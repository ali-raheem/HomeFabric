"""Database and file-store integrity diagnostics."""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.models import F

from homefabric.documents import storage
from homefabric.documents.models import Document, StoredBlob


def check(*, verify_checksums: bool = False, clean: bool = False) -> dict:
    report: dict = {"ok": True, "problems": [], "cleaned": []}

    def problem(kind, detail):
        report["ok"] = False
        report["problems"].append({"kind": kind, "detail": detail})

    with connection.cursor() as cur:
        if connection.vendor == "sqlite":
            cur.execute("PRAGMA integrity_check")
            result = [r[0] for r in cur.fetchall()]
            if result != ["ok"]:
                problem("database", "; ".join(result[:20]))
            cur.execute("PRAGMA foreign_key_check")
            for row in cur.fetchall()[:50]:
                problem("foreign_key", f"{row[0]} rowid {row[1]} references missing {row[2]}")

    known = {}
    for blob in StoredBlob.objects.all().iterator():
        known[blob.storage_key] = blob
        path = storage.path_for(blob.storage_key)
        if not path.exists():
            problem("missing_file", f"blob {blob.storage_key} (sha256 {blob.sha256[:12]}…) "
                    "is referenced but missing")
            continue
        if path.stat().st_size != blob.size:
            problem("size_mismatch", f"blob {blob.storage_key} has unexpected size")
        elif verify_checksums:
            sha, _ = storage.hash_file(path)
            if sha != blob.sha256:
                problem("checksum_mismatch", f"blob {blob.storage_key} content changed")

    for key, path in storage.iter_blob_files():
        if key not in known:
            if clean:
                path.unlink(missing_ok=True)
                report["cleaned"].append(f"orphaned file {key}")
            else:
                problem("orphaned_file", f"{key} has no database record")

    for blob in StoredBlob.objects.filter(revisions__isnull=True):
        problem("orphaned_blob", f"blob record {blob.pk} is not used by any revision")

    for doc in Document.objects.filter(current_revision__isnull=True):
        problem("document_without_revision", f"document {doc.pk} '{doc.display_name}'")

    from homefabric.assets.models import EntityRelationship
    from homefabric.documents.models import EntityDocument
    from homefabric.maintenance.models import Task

    cross = EntityRelationship.objects.exclude(source__household=F("target__household")).count()
    cross += EntityRelationship.objects.exclude(household=F("source__household")).count()
    cross += EntityDocument.objects.exclude(household=F("document__household")).count()
    cross += Task.objects.exclude(household=F("entity__household")).count()
    if cross:
        problem("cross_household", f"{cross} record(s) reference another household")

    cutoff = time.time() - dt.timedelta(days=1).total_seconds()
    staging = Path(settings.STAGING_DIR)
    if staging.exists():
        for f in staging.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                if clean:
                    f.unlink(missing_ok=True)
                    report["cleaned"].append(f"stale staging file {f.name}")
                else:
                    problem("stale_staging", f"{f.name} left by an interrupted upload")
    report["counts"] = {"blobs": len(known), "documents": Document.objects.count()}
    return report
