"""Build a printable house manual from a household's records.

Structure:

1. Locations: one chapter per top-level property, then a section for each
   floor and room in tree order. Items get a page inside the location they
   are in.
2. Systems: one chapter per top-level system. Members that are printed in a
   room are cross-referenced; unlocated members get their page here.
3. Policies and agreements.
4. Other items (no location and no system).

Each entity has exactly one full page. Everywhere else it appears as a
numbered cross-reference, so the printed manual reads like a book without
the app.
"""

from __future__ import annotations

import base64
import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass, field

from django.db.models import F, Window
from django.db.models.functions import RowNumber
from django.urls import reverse
from django.utils import timezone

from homefabric.assets.models import (
    COVER_KINDS,
    LOCATION_KINDS,
    BaseKind,
    Entity,
    EntityRelationship,
    EntityStatus,
    RelationKind,
)
from homefabric.core.dates import household_today
from homefabric.core.money import format_money
from homefabric.core.text import render_markdown
from homefabric.documents.models import ActivityDocument, EntityDocument
from homefabric.maintenance.models import UNFINISHED, ActivityEntry, MaintenanceSchedule, Task

# Fill / border colours per kind. Dark text on light fills keeps contrast
# high on screen and paper; every node also shows its kind in words.
KIND_COLOURS = {
    BaseKind.PROPERTY: ("#c7d2fe", "#3730a3"),
    BaseKind.FLOOR: ("#bae6fd", "#075985"),
    BaseKind.ROOM: ("#bbf7d0", "#166534"),
    BaseKind.SYSTEM: ("#fde68a", "#92400e"),
    BaseKind.EQUIPMENT: ("#fecaca", "#991b1b"),
    BaseKind.COMPONENT: ("#fbcfe8", "#9d174d"),
    BaseKind.POLICY: ("#ddd6fe", "#5b21b6"),
    BaseKind.AGREEMENT: ("#e5e7eb", "#374151"),
}
KIND_ORDER = {k: i for i, k in enumerate(KIND_COLOURS)}
INLINE_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_EMBED_BYTES = 1_500_000
MAX_EMBED_TOTAL = 30_000_000
MAX_PHOTOS_PER_ITEM = 4
CHART_ROWS_PER_BLOCK = 38
CHART_MAX_ROWS = 600

HISTORY_CHOICES = {"none": 0, "recent": 5, "all": None}


@dataclass
class Ref:
    name: str
    kind: str
    number: str | None = None
    anchor: str | None = None
    note: str = ""


@dataclass
class Page:
    title: str
    number: str
    anchor: str
    depth: int
    part: str
    entity: Entity | None = None
    kind: str = ""
    is_chapter: bool = False
    intro: str = ""
    path: list = field(default_factory=list)
    part_of: list = field(default_factory=list)
    contents: list = field(default_factory=list)
    members: list = field(default_factory=list)
    components: list = field(default_factory=list)
    associated: list = field(default_factory=list)
    covers: list = field(default_factory=list)
    covered_by: list = field(default_factory=list)
    details: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    description: str = ""
    documents: list = field(default_factory=list)
    photos: list = field(default_factory=list)
    schedules: list = field(default_factory=list)
    tasks: list = field(default_factory=list)
    history: list = field(default_factory=list)
    history_truncated: bool = False


@dataclass
class ContentsEntry:
    number: str
    title: str
    anchor: str
    depth: int
    kind: str
    part: str


@dataclass
class ChartBlock:
    width: int
    height: int
    rows: list
    lines: list


@dataclass
class Chart:
    title: str
    blocks: list
    note: str = ""


@dataclass
class Manual:
    household: object
    generated_at: dt.datetime
    generated_by: str
    today: dt.date
    options: dict
    parts: list
    contents: list
    pages: list
    charts: list
    upcoming: list
    schedules: list
    policies: list
    legend: list
    bundle: list  # (revision, archive path) for ZIP output
    counts: dict


def _anchor(entity) -> str:
    return f"e-{entity.pk.hex[:12]}"


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:80] or "file"


class ManualBuilder:
    def __init__(self, household, *, actor=None, include_archived=False, history="recent",
                 photos=True, link_mode="app", page_per_item=True):
        if link_mode not in ("app", "zip", "inline"):
            raise ValueError("link_mode must be app, zip or inline")
        self.household = household
        self.actor = actor
        self.include_archived = include_archived
        self.history_limit = HISTORY_CHOICES.get(history, 5)
        self.history_option = history if history in HISTORY_CHOICES else "recent"
        self.photos = photos
        self.link_mode = link_mode
        self.page_per_item = page_per_item
        self.today = household_today(household)
        self.printed: dict = {}
        self.pages: list[Page] = []
        self.contents: list[ContentsEntry] = []
        self.bundle: dict = {}
        self.embedded_bytes = 0

    # --- loading ----------------------------------------------------------

    def _load(self):
        hh = self.household
        qs = Entity.objects.filter(household=hh).select_related("entity_type").prefetch_related(
            "tags")
        if not self.include_archived:
            qs = qs.filter(status=EntityStatus.ACTIVE)
        self.ents = {e.pk: e for e in qs}
        self.loc_parent, self.loc_children = {}, defaultdict(list)
        self.part_parents, self.part_children = defaultdict(list), defaultdict(list)
        self.assoc = defaultdict(list)
        self.covers, self.covered_by = defaultdict(list), defaultdict(list)
        labels = {}
        for r in EntityRelationship.objects.filter(household=hh).values(
                "source_id", "target_id", "kind", "label"):
            s, t = r["source_id"], r["target_id"]
            if s not in self.ents or t not in self.ents:
                continue
            k = r["kind"]
            if k == RelationKind.LOCATED_IN:
                self.loc_parent[s] = t
                self.loc_children[t].append(s)
            elif k == RelationKind.PART_OF:
                self.part_parents[s].append(t)
                self.part_children[t].append(s)
            elif k == RelationKind.ASSOCIATED:
                self.assoc[s].append(t)
                self.assoc[t].append(s)
                labels[(s, t)] = labels[(t, s)] = r["label"]
            elif k == RelationKind.COVERS:
                self.covers[s].append(t)
                self.covered_by[t].append(s)
        self.assoc_labels = labels

        self.docs = defaultdict(list)
        for link in EntityDocument.objects.filter(household=hh).select_related(
                "document", "document__current_revision", "document__current_revision__blob"
        ).order_by("document__category", "document__display_name"):
            if link.entity_id in self.ents and link.document.current_revision_id:
                self.docs[link.entity_id].append(link.document)

        self.schedules = defaultdict(list)
        next_due = {}
        for t in Task.objects.filter(household=hh, status__in=UNFINISHED,
                                     schedule__isnull=False).order_by("due_date"):
            next_due.setdefault(t.schedule_id, t.due_date)
        for s in MaintenanceSchedule.objects.filter(household=hh).order_by("-enabled", "title"):
            if s.entity_id in self.ents:
                s.next_due = next_due.get(s.pk)
                self.schedules[s.entity_id].append(s)

        self.tasks = defaultdict(list)
        for t in Task.objects.filter(household=hh, status__in=UNFINISHED).select_related(
                "assignee").order_by("due_date", "title"):
            if t.entity_id in self.ents:
                self.tasks[t.entity_id].append(t)

        self.history = defaultdict(list)
        self.history_counts = defaultdict(int)
        if self.history_limit != 0:
            hist = ActivityEntry.objects.filter(household=hh, superseded=False,
                                                entity_id__in=list(self.ents))
            from django.db.models import Count

            self.history_counts = {r["entity_id"]: r["n"] for r in hist.values(
                "entity_id").annotate(n=Count("id")).order_by()}
            if self.history_limit:
                hist = hist.annotate(rank=Window(
                    RowNumber(), partition_by=[F("entity_id")],
                    order_by=[F("occurred_on").desc(), F("created_at").desc()],
                )).filter(rank__lte=self.history_limit)
            entries = list(hist.select_related("author").order_by(
                "entity_id", "-occurred_on", "-created_at"))
            attach = defaultdict(list)
            for link in ActivityDocument.objects.filter(
                    activity_id__in=[e.pk for e in entries]).select_related(
                    "revision", "revision__document", "revision__blob"):
                attach[link.activity_id].append(link.revision)
            for e in entries:
                e.attachments = attach.get(e.pk, [])
                self.history[e.entity_id].append(e)

    # --- helpers ----------------------------------------------------------

    def _kind(self, eid) -> str:
        return self.ents[eid].entity_type.base_kind

    def _sorted(self, ids):
        """Sibling order: explicit sort position first, then kind, then name."""
        def key(i):
            e = self.ents[i]
            pos = e.sort_order
            return (pos is None, pos or 0, KIND_ORDER.get(e.entity_type.base_kind, 99),
                    e.name.lower())
        return sorted(ids, key=key)

    def _ref(self, eid, note="") -> Ref:
        e = self.ents[eid]
        num, anchor = self.printed.get(eid, (None, None))
        return Ref(e.name, e.entity_type.base_kind, num, anchor, note)

    def _doc_href(self, revision) -> str | None:
        if self.link_mode == "app":
            return reverse("documents:download", args=[revision.pk])
        if self.link_mode == "zip":
            path = f"documents/{revision.document_id.hex[:8]}-{_safe_name(revision.original_filename)}"
            self.bundle[revision.pk] = (revision, path)
            return path
        return None

    def _photo_src(self, revision) -> str | None:
        if self.link_mode == "app":
            return reverse("documents:download", args=[revision.pk]) + "?inline=1"
        if self.link_mode == "zip":
            return self._doc_href(revision)
        if revision.size > MAX_EMBED_BYTES or \
                self.embedded_bytes + revision.size > MAX_EMBED_TOTAL:
            return None
        from homefabric.documents import storage

        path = storage.path_for(revision.blob.storage_key)
        if not path.exists():
            return None
        data = path.read_bytes()
        self.embedded_bytes += len(data)
        return f"data:{revision.mime_type};base64,{base64.b64encode(data).decode()}"

    # --- pages ------------------------------------------------------------

    def _new_page(self, eid, number, depth, part, is_chapter=False) -> Page:
        e = self.ents[eid]
        anchor = _anchor(e)
        self.printed[eid] = (number, anchor)
        page = Page(title=e.name, number=number, anchor=anchor, depth=depth, part=part, entity=e,
                    kind=e.entity_type.base_kind, is_chapter=is_chapter)
        self.pages.append(page)
        self.contents.append(ContentsEntry(number, e.name, anchor, depth, page.kind, part))
        return page

    def _fill(self, page: Page):
        e = page.entity
        eid = e.pk
        path, seen, cur = [], {eid}, self.loc_parent.get(eid)
        while cur is not None and cur not in seen:
            path.append(cur)
            seen.add(cur)
            cur = self.loc_parent.get(cur)
        page.path = [self._ref(i) for i in reversed(path)]
        page.part_of = [self._ref(i) for i in self._sorted(self.part_parents.get(eid, []))]
        page.contents = [self._ref(i) for i in self._sorted(self.loc_children.get(eid, []))]
        page.components = [self._ref(i) for i in self._sorted(self.part_children.get(eid, []))]
        page.associated = [self._ref(i, self.assoc_labels.get((eid, i), ""))
                           for i in self._sorted(set(self.assoc.get(eid, [])))]
        page.covers = [self._ref(i) for i in self._sorted(self.covers.get(eid, []))]
        page.covered_by = [self._ref(i) for i in self._sorted(self.covered_by.get(eid, []))]
        page.tags = sorted(t.name for t in e.tags.all())
        page.description = render_markdown(e.description) if e.description else ""
        d = []
        for label, value in (
            ("Manufacturer", e.manufacturer),
            ("Model", e.model_number), ("Serial number", e.serial_number),
            ("Purchased", e.purchase_date), ("Installed", e.installation_date),
            ("Warranty ends", e.warranty_end),
            ("Cost", format_money(e.cost_minor, e.cost_currency) if e.cost_minor is not None
             else ""),
            ("Provider", e.provider), ("Reference", e.reference_number),
            ("Starts", e.start_date), ("Expires / ends", e.end_date),
            ("Renewal / review", e.renewal_date),
        ):
            if value:
                d.append((label, value.strftime("%d %b %Y") if isinstance(value, dt.date)
                          else str(value)))
        for k, v in (e.attributes or {}).items():
            if v not in (None, ""):
                d.append((k, str(v)))
        if e.status == EntityStatus.ARCHIVED:
            d.append(("Status", "Archived (no longer in use)"))
        page.details = d
        for doc in self.docs.get(eid, []):
            rev = doc.current_revision
            if self.photos and rev.mime_type in INLINE_IMAGE_TYPES and (
                    doc.category == "photo" or rev.mime_type.startswith("image/")) and \
                    len(page.photos) < MAX_PHOTOS_PER_ITEM:
                src = self._photo_src(rev)
                if src:
                    page.photos.append({"src": src, "caption": doc.display_name})
            page.documents.append({
                "name": doc.display_name, "category": doc.get_category_display(),
                "filename": rev.original_filename, "size": rev.size,
                "revision": rev.revision_number, "href": self._doc_href(rev),
                "description": doc.description,
            })
        page.schedules = self.schedules.get(eid, [])
        page.tasks = [{"title": t.title, "due": t.due_date, "assignee": t.assignee,
                       "status": t.get_status_display(), "booking": t.kind == "booking",
                       "overdue": t.due_date is not None and t.due_date < self.today}
                      for t in self.tasks.get(eid, [])]
        entries = self.history.get(eid, [])
        page.history = [{
            "date": a.occurred_on, "type": a.get_event_type_display(), "title": a.headline,
            "provider": a.provider, "author": a.author.label if a.author else "",
            "cost": format_money(a.amount_minor, a.currency) if a.amount_minor is not None
            else "",
            "notes": render_markdown(a.notes) if a.notes else "",
            "corrected": bool(a.correction_of_id),
            "attachments": [{"name": r.document.display_name, "href": self._doc_href(r)}
                            for r in a.attachments],
        } for a in entries]
        page.history_truncated = self.history_counts.get(eid, 0) > len(entries)

    # --- placement --------------------------------------------------------

    def _place_location(self, eid, number, depth, part):
        self._new_page(eid, number, depth, part, is_chapter=depth == 1)
        kids = self._sorted(self.loc_children.get(eid, []))
        items = [k for k in kids if k not in self.printed and self._kind(k) not in LOCATION_KINDS
                 and self._kind(k) != BaseKind.SYSTEM]
        subs = [k for k in kids if k not in self.printed and self._kind(k) in LOCATION_KINDS]
        i = 1
        for k in items:
            self._place_item(k, f"{number}.{i}", depth + 1, part)
            i += 1
        for k in subs:
            self._place_location(k, f"{number}.{i}", depth + 1, part)
            i += 1

    def _place_item(self, eid, number, depth, part, is_chapter=False):
        self._new_page(eid, number, depth, part, is_chapter=is_chapter)
        i = 1
        nested = [k for k in self._sorted(self.loc_children.get(eid, []))
                  if k not in self.printed and self._kind(k) != BaseKind.SYSTEM]
        loose_parts = [k for k in self._sorted(self.part_children.get(eid, []))
                       if k not in self.printed and k not in self.loc_parent
                       and self._kind(k) != BaseKind.SYSTEM]
        for k in nested + loose_parts:
            if k in self.printed:
                continue
            if self._kind(k) in LOCATION_KINDS:
                self._place_location(k, f"{number}.{i}", depth + 1, part)
            else:
                self._place_item(k, f"{number}.{i}", depth + 1, part)
            i += 1

    def _place_system(self, eid, number, depth, part):
        self._new_page(eid, number, depth, part, is_chapter=depth == 1)
        i = 1
        for k in self._sorted(self.part_children.get(eid, [])):
            if k in self.printed:
                continue
            if self._kind(k) == BaseKind.SYSTEM:
                self._place_system(k, f"{number}.{i}", depth + 1, part)
                i += 1
            elif k not in self.loc_parent:
                self._place_item(k, f"{number}.{i}", depth + 1, part)
                i += 1

    def _section(self, title, number, part, intro="") -> Page:
        anchor = f"s-{_safe_name(title).lower()}"
        page = Page(title=title, number=number, anchor=anchor, depth=1, part=part,
                    is_chapter=True, intro=intro)
        self.pages.append(page)
        self.contents.append(ContentsEntry(number, title, anchor, 1, "", part))
        return page

    def _layout(self):
        chapter = 0
        parts = []
        roots = self._sorted([i for i, e in self.ents.items()
                              if e.entity_type.base_kind in LOCATION_KINDS
                              and i not in self.loc_parent])
        if roots:
            parts.append(("locations", "Rooms and locations", True))
            for r in roots:
                chapter += 1
                self._place_location(r, str(chapter), 1, "locations")
        systems = self._sorted([i for i, e in self.ents.items()
                                if e.entity_type.base_kind == BaseKind.SYSTEM
                                and not any(self._kind(p) == BaseKind.SYSTEM
                                            for p in self.part_parents.get(i, []))])
        if systems:
            parts.append(("systems", "Systems", True))
            for s in systems:
                if s in self.printed:
                    continue
                chapter += 1
                self._place_system(s, str(chapter), 1, "systems")
        covers = self._sorted([i for i, e in self.ents.items()
                               if e.entity_type.base_kind in COVER_KINDS])
        if covers:
            parts.append(("policies", "Policies and agreements", False))
            chapter += 1
            self._section("Policies and agreements", str(chapter), "policies",
                          "Insurance, warranties, mortgages and other agreements, with what "
                          "each one covers.")
            for n, c in enumerate(covers, start=1):
                self._place_item(c, f"{chapter}.{n}", 2, "policies")
        rest = self._sorted([i for i in self.ents if i not in self.printed])
        if rest:
            parts.append(("other", "Other items", False))
            chapter += 1
            self._section("Other items", str(chapter), "other",
                          "Items not yet assigned to a room or system.")
            n = 0
            for i in rest:
                if i in self.printed:
                    continue
                n += 1
                self._place_item(i, f"{chapter}.{n}", 2, "other")
        return parts

    # --- chart ------------------------------------------------------------

    def _tree_rows(self, roots, children_of, include) -> list:
        """Depth-first rows. An item with several parents (one boiler in two
        systems) appears under each; loops are cut per branch."""
        rows = []

        def walk(eid, depth, ancestors):
            if eid in ancestors or len(rows) > 50_000:
                return
            rows.append((depth, eid))
            for k in self._sorted(children_of(eid)):
                if include(k):
                    walk(k, depth + 1, ancestors | {eid})

        for r in roots:
            walk(r, 0, frozenset())
        return rows

    def _chart(self, title, rows, note="") -> Chart:
        indent, row_h, box_w, gap = 22, 26, 300, 4
        blocks = []
        for start in range(0, len(rows), CHART_ROWS_PER_BLOCK):
            chunk = rows[start:start + CHART_ROWS_PER_BLOCK]
            max_depth = max(d for d, _ in chunk)
            width = max_depth * indent + box_w + 10
            items, lines = [], []
            last_at_depth = {}
            for i, (depth, eid) in enumerate(chunk):
                y = i * row_h + gap
                mid = y + (row_h - gap) / 2
                x = depth * indent + 2
                e = self.ents[eid]
                kind = e.entity_type.base_kind
                fill, stroke = KIND_COLOURS.get(kind, ("#f3f4f6", "#374151"))
                num, anchor = self.printed.get(eid, (None, None))
                name = e.name if len(e.name) <= 30 else e.name[:29] + "…"
                items.append({"x": x, "y": y, "w": box_w, "h": row_h - gap, "fill": fill,
                              "stroke": stroke, "tx": x + 8, "ty": mid + 4,
                              "kx": x + box_w - 8, "label": name,
                              "kind": e.entity_type.name, "number": num or "",
                              "anchor": anchor})
                if depth > 0:
                    px = (depth - 1) * indent + 10
                    top = last_at_depth.get(depth - 1, -1)
                    y0 = top + row_h - gap if top >= 0 else 0
                    lines.append((px, y0, px, mid))
                    lines.append((px, mid, x, mid))
                last_at_depth[depth] = y
                for d in list(last_at_depth):
                    if d > depth:
                        del last_at_depth[d]
            blocks.append(ChartBlock(width=width, height=len(chunk) * row_h + gap,
                                     rows=items, lines=lines))
        return Chart(title=title, blocks=blocks, note=note)

    def _charts(self) -> list:
        charts = []
        loc_roots = [i for i, e in self.ents.items()
                     if e.entity_type.base_kind in LOCATION_KINDS and i not in self.loc_parent]
        rows = self._tree_rows(self._sorted(loc_roots), lambda i: self.loc_children.get(i, []),
                               lambda k: True)
        note = ""
        if len(rows) > CHART_MAX_ROWS:
            rows = self._tree_rows(self._sorted(loc_roots),
                                   lambda i: self.loc_children.get(i, []),
                                   lambda k: self._kind(k) in LOCATION_KINDS)
            note = "Rooms and locations only; see each chapter for its items."
        if rows:
            charts.append(self._chart("Where things are", rows, note))
        sys_roots = [i for i, e in self.ents.items() if e.entity_type.base_kind == BaseKind.SYSTEM
                     and not any(self._kind(p) == BaseKind.SYSTEM
                                 for p in self.part_parents.get(i, []))]
        rows = self._tree_rows(self._sorted(sys_roots), lambda i: self.part_children.get(i, []),
                               lambda k: True)
        if len(rows) > CHART_MAX_ROWS:
            rows = rows[:CHART_MAX_ROWS]
            note = f"First {CHART_MAX_ROWS} entries shown."
        else:
            note = ""
        if rows:
            charts.append(self._chart("How things work together (systems)", rows, note))
        return charts

    # --- at a glance --------------------------------------------------------

    def _glance(self):
        horizon = self.today + dt.timedelta(days=90)
        upcoming = []
        for eid, tasks in self.tasks.items():
            for t in tasks:
                if t.due_date and t.due_date <= horizon:
                    upcoming.append({"due": t.due_date, "title": t.title,
                                     "item": self._ref(eid),
                                     "overdue": t.due_date < self.today,
                                     "assignee": t.assignee})
        upcoming.sort(key=lambda r: r["due"])
        schedules = []
        for eid, items in self.schedules.items():
            for s in items:
                if s.enabled:
                    schedules.append({"title": s.title, "describe": s.describe,
                                      "next_due": s.next_due, "item": self._ref(eid)})
        schedules.sort(key=lambda r: (r["next_due"] or dt.date.max, r["title"]))
        policies = []
        for eid, e in self.ents.items():
            if e.entity_type.base_kind in COVER_KINDS:
                policies.append({"item": self._ref(eid), "provider": e.provider,
                                 "reference": e.reference_number, "end": e.end_date,
                                 "renewal": e.renewal_date,
                                 "covers": [self._ref(i) for i in self.covers.get(eid, [])]})
        policies.sort(key=lambda r: r["item"].name.lower())
        return upcoming[:300], schedules, policies

    # --- entry point --------------------------------------------------------

    def build(self) -> Manual:
        self._load()
        layout = self._layout()
        for page in self.pages:
            if page.entity is not None:
                self._fill(page)
        upcoming, schedules, policies = self._glance()
        legend = [(BaseKind(k).label, fill, stroke) for k, (fill, stroke) in KIND_COLOURS.items()]
        actor = self.actor.label if self.actor is not None else ""
        # Group once so templates do not rescan every page per part.
        parts = [{"key": key, "label": label, "divider": divider,
                  "pages": [p for p in self.pages if p.part == key],
                  "contents": [c for c in self.contents if c.part == key]}
                 for key, label, divider in layout]
        return Manual(
            household=self.household, generated_at=timezone.now(), generated_by=actor,
            today=self.today,
            options={"history": self.history_option, "include_archived": self.include_archived,
                     "photos": self.photos, "page_per_item": self.page_per_item,
                     "link_mode": self.link_mode},
            parts=parts, contents=self.contents, pages=self.pages, charts=self._charts(),
            upcoming=upcoming, schedules=schedules, policies=policies, legend=legend,
            bundle=list(self.bundle.values()),
            counts={"items": len(self.ents), "pages": len(self.pages)},
        )
