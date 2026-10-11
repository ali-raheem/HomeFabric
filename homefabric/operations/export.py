"""Readable per-household export: versioned JSON, CSV summaries and documents.

This is a portable data extract, not an operational backup. Credentials and
provider secrets are never included.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import zipfile
from pathlib import Path

from django.db.models import Q

from homefabric import __version__
from homefabric.assets.models import Entity, EntityRelationship, EntityType, Tag
from homefabric.core.money import format_money
from homefabric.documents import storage
from homefabric.documents.models import (
    ActivityDocument,
    Document,
    DocumentRevision,
    EntityDocument,
    TaskDocument,
)
from homefabric.maintenance.models import ActivityEntry, MaintenanceSchedule, ReminderRule, Task

FORMAT = "homefabric-household-export"
FORMAT_VERSION = 1


def _s(value):
    if value is None:
        return None
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    return str(value)


def _user(u):
    return u.username if u else None


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]", "_", name)[:120] or "file"


def build_export(household) -> dict:
    hh = household
    entities = Entity.objects.filter(household=hh).select_related(
        "entity_type", "created_by", "updated_by").prefetch_related("tags")
    revisions = DocumentRevision.objects.filter(document__household=hh).select_related(
        "uploaded_by")
    revs_by_doc: dict = {}
    for r in revisions:
        revs_by_doc.setdefault(r.document_id, []).append(r)
    act_docs: dict = {}
    for link in ActivityDocument.objects.filter(household=hh):
        act_docs.setdefault(link.activity_id, []).append(str(link.revision_id))
    data = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "app_version": __version__,
        "exported_at": dt.datetime.now(dt.UTC).isoformat(),
        "household": {"id": str(hh.pk), "name": hh.name, "timezone": hh.timezone,
                      "currency": hh.currency},
        "members": [
            {"username": m.user.username, "display_name": m.user.display_name,
             "email": m.user.email, "role": m.role, "active": m.is_active and m.user.is_active}
            for m in hh.memberships.select_related("user")
        ],
        "entity_types": [
            {"id": et.pk, "name": et.name, "base_kind": et.base_kind, "seed": et.is_seed}
            for et in EntityType.objects.filter(
                Q(entities__household=hh) | Q(household=hh)).distinct()
        ],
        "tags": [t.name for t in Tag.objects.filter(household=hh)],
        "entities": [
            {
                "id": str(e.pk), "name": e.name, "type": e.entity_type.name,
                "base_kind": e.entity_type.base_kind, "description": e.description,
                "status": e.status, "tags": sorted(t.name for t in e.tags.all()),
                "sort_order": e.sort_order,
                "manufacturer": e.manufacturer, "model_number": e.model_number,
                "serial_number": e.serial_number, "purchase_date": _s(e.purchase_date),
                "installation_date": _s(e.installation_date),
                "warranty_end": _s(e.warranty_end), "cost_minor": e.cost_minor,
                "cost_currency": e.cost_currency, "provider": e.provider,
                "reference_number": e.reference_number, "start_date": _s(e.start_date),
                "end_date": _s(e.end_date), "renewal_date": _s(e.renewal_date),
                "attributes": e.attributes, "created_at": _s(e.created_at),
                "created_by": _user(e.created_by), "updated_at": _s(e.updated_at),
                "archived_at": _s(e.archived_at),
            }
            for e in entities
        ],
        "relationships": [
            {"id": str(r.pk), "source": str(r.source_id), "target": str(r.target_id),
             "kind": r.kind, "label": r.label, "created_at": _s(r.created_at)}
            for r in EntityRelationship.objects.filter(household=hh)
        ],
        "documents": [
            {
                "id": str(d.pk), "display_name": d.display_name, "category": d.category,
                "description": d.description,
                "current_revision": str(d.current_revision_id) if d.current_revision_id else None,
                "revisions": [
                    {"id": str(r.pk), "number": r.revision_number,
                     "filename": r.original_filename, "mime_type": r.mime_type, "size": r.size,
                     "sha256": r.sha256, "uploaded_by": _user(r.uploaded_by),
                     "uploaded_at": _s(r.uploaded_at), "note": r.note,
                     "path": f"documents/{d.pk}/r{r.revision_number}-{_safe_name(r.original_filename)}"}
                    for r in sorted(revs_by_doc.get(d.pk, []), key=lambda r: r.revision_number)
                ],
            }
            for d in Document.objects.filter(household=hh)
        ],
        "entity_documents": [
            {"entity": str(link.entity_id), "document": str(link.document_id)}
            for link in EntityDocument.objects.filter(household=hh)
        ],
        "task_documents": [
            {"task": str(link.task_id), "document": str(link.document_id)}
            for link in TaskDocument.objects.filter(household=hh)
        ],
        "activity": [
            {
                "id": str(a.pk), "entity": str(a.entity_id), "event_type": a.event_type,
                "occurred_on": _s(a.occurred_on), "occurred_time": _s(a.occurred_time),
                "title": a.title, "notes": a.notes, "provider": a.provider,
                "amount_minor": a.amount_minor, "currency": a.currency,
                "task": str(a.task_id) if a.task_id else None, "author": _user(a.author),
                "created_at": _s(a.created_at),
                "correction_of": str(a.correction_of_id) if a.correction_of_id else None,
                "correction_reason": a.correction_reason, "superseded": a.superseded,
                "document_revisions": act_docs.get(a.pk, []),
            }
            for a in ActivityEntry.objects.filter(household=hh).select_related("author")
        ],
        "schedules": [
            {"id": str(s.pk), "entity": str(s.entity_id), "title": s.title,
             "description": s.description, "mode": s.mode, "interval_count": s.interval_count,
             "interval_unit": s.interval_unit, "anchor_date": _s(s.anchor_date),
             "timezone": s.timezone, "enabled": s.enabled}
            for s in MaintenanceSchedule.objects.filter(household=hh)
        ],
        "tasks": [
            {"id": str(t.pk), "entity": str(t.entity_id),
             "schedule": str(t.schedule_id) if t.schedule_id else None,
             "occurrence_key": t.occurrence_key, "title": t.title, "description": t.description,
             "kind": t.kind, "status": t.status, "due_date": _s(t.due_date),
             "assignee": _user(t.assignee), "completed_on": _s(t.completed_on),
             "result": t.result, "status_reason": t.status_reason,
             "related_task": str(t.related_task_id) if t.related_task_id else None}
            for t in Task.objects.filter(household=hh).select_related("assignee")
        ],
        "reminder_rules": [
            {"id": str(r.pk), "task": str(r.task_id) if r.task_id else None,
             "schedule": str(r.schedule_id) if r.schedule_id else None,
             "lead_days": r.lead_days, "time_of_day": _s(r.time_of_day),
             "recipient_mode": r.recipient_mode, "enabled": r.enabled}
            for r in ReminderRule.objects.filter(household=hh)
        ],
    }
    return data


def _csv(rows, headers) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(["" if v is None else v for v in row])
    return buf.getvalue()


README = """HomeFabric household export
===========================

export.json  Complete structured data (format "{fmt}" version {ver}). Stable IDs
             are preserved; relationships, history and documents refer to them.
*.csv        Human-readable summaries for spreadsheets.
documents/   Every document revision, named r<revision>-<original filename>.

This export excludes account passwords and notification credentials. It is a
portable data extract, not an operational backup; use 'manage.py backup' for that.
"""


def write_export(household, output) -> Path:
    output = Path(output)
    data = build_export(household)
    entity_names = {e["id"]: e["name"] for e in data["entities"]}
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        zf.writestr("README.txt", README.format(fmt=FORMAT, ver=FORMAT_VERSION))
        zf.writestr("export.json", json.dumps(data, indent=2, ensure_ascii=False))
        zf.writestr("entities.csv", _csv(
            [(e["id"], e["name"], e["type"], e["status"], ", ".join(e["tags"]),
              e["manufacturer"], e["model_number"], e["serial_number"],
              e["installation_date"], e["warranty_end"],
              format_money(e["cost_minor"], e["cost_currency"]))
             for e in data["entities"]],
            ["id", "name", "type", "status", "tags", "manufacturer", "model", "serial",
             "installed", "warranty_end", "cost"]))
        zf.writestr("tasks.csv", _csv(
            [(t["id"], entity_names.get(t["entity"], ""), t["title"], t["status"],
              t["due_date"], t["assignee"], t["completed_on"]) for t in data["tasks"]],
            ["id", "item", "title", "status", "due_date", "assignee", "completed_on"]))
        zf.writestr("activity.csv", _csv(
            [(a["id"], entity_names.get(a["entity"], ""), a["occurred_on"], a["event_type"],
              a["title"], a["provider"], format_money(a["amount_minor"], a["currency"]),
              a["author"], "yes" if a["superseded"] else "", a["notes"])
             for a in data["activity"]],
            ["id", "item", "date", "type", "title", "provider", "amount", "author",
             "superseded", "notes"]))
        zf.writestr("documents.csv", _csv(
            [(d["id"], d["display_name"], d["category"], len(d["revisions"]),
              d["revisions"][-1]["filename"] if d["revisions"] else "")
             for d in data["documents"]],
            ["id", "name", "category", "revisions", "latest_filename"]))
        revs = DocumentRevision.objects.filter(document__household=household).select_related("blob")
        paths = {r["id"]: r["path"] for d in data["documents"] for r in d["revisions"]}
        for rev in revs:
            src = storage.path_for(rev.blob.storage_key)
            if src.exists():
                zf.write(src, paths[str(rev.pk)], compress_type=zipfile.ZIP_STORED)
    return output
