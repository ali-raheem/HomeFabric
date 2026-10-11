"""Entity and relationship business rules shared by web views and CLI."""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from homefabric.accounts.models import Household
from homefabric.assets.models import (
    ASSET_KINDS,
    COVER_KINDS,
    LOCATION_KINDS,
    SIBLING_ORDER,
    BaseKind,
    Entity,
    EntityRelationship,
    EntityStatus,
    EntityType,
    RelationKind,
    Tag,
)
from homefabric.core import audit
from homefabric.core.concurrency import versioned_update
from homefabric.core.errors import ArchivedError, DomainError

MAX_WALK = 10_000
EDITABLE_FIELDS = [
    "name", "description", "entity_type", "manufacturer", "model_number", "serial_number",
    "purchase_date", "installation_date", "warranty_end", "cost_minor", "cost_currency",
    "provider", "reference_number", "start_date", "end_date", "renewal_date", "attributes",
    "sort_order",
]

# --- attributes -----------------------------------------------------------

MAX_ATTRIBUTES = 50


def validate_attributes(value) -> dict:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise DomainError("Attributes must be a set of name/value pairs.")
    if len(value) > MAX_ATTRIBUTES:
        raise DomainError(f"At most {MAX_ATTRIBUTES} attributes are allowed.")
    clean = {}
    for key, val in value.items():
        key = str(key).strip()
        if not key or len(key) > 60:
            raise DomainError("Attribute names must be 1–60 characters.")
        if isinstance(val, str):
            if len(val) > 500:
                raise DomainError(f"Attribute '{key}' is longer than 500 characters.")
        elif not (val is None or isinstance(val, (bool, int, float))):
            raise DomainError(f"Attribute '{key}' must be text, a number, or true/false.")
        clean[key] = val
    return clean


def parse_attribute_lines(text: str) -> dict:
    """Parse ``name: value`` lines from a textarea."""
    result = {}
    for lineno, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if ":" not in line:
            raise DomainError(f"Attribute line {lineno} must look like 'name: value'.")
        key, _, val = line.partition(":")
        result[key.strip()] = val.strip()
    return validate_attributes(result)


def format_attribute_lines(attrs: dict) -> str:
    return "\n".join(f"{k}: {'' if v is None else v}" for k, v in (attrs or {}).items())


# --- types and tags -------------------------------------------------------

def types_for(household: Household):
    from homefabric.assets.seed import ensure_seed_types

    ensure_seed_types()
    return EntityType.objects.filter(Q(household__isnull=True) | Q(household=household)).order_by(
        "base_kind", "name"
    )


def create_entity_type(household: Household, *, name: str, base_kind: str, actor,
                       description: str = "") -> EntityType:
    name = name.strip()
    if base_kind not in BaseKind.values:
        raise DomainError("Choose a valid base kind.")
    if types_for(household).filter(name__iexact=name).exists():
        raise DomainError(f"A type named '{name}' already exists.")
    et = EntityType.objects.create(household=household, name=name, base_kind=base_kind,
                                   description=description)
    audit.record(actor, "entity_type.create", et, household=household)
    return et


def resolve_tags(household: Household, names) -> list[Tag]:
    tags = []
    seen = set()
    for raw in names:
        name = raw.strip()[:60]
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        tag = Tag.objects.filter(household=household, name__iexact=name).first()
        if tag is None:
            tag = Tag.objects.create(household=household, name=name)
        tags.append(tag)
    return tags


def split_tags(text: str) -> list[str]:
    return [t.strip() for t in (text or "").split(",") if t.strip()]


# --- entities -------------------------------------------------------------

def _check_type(household: Household, entity_type: EntityType):
    if entity_type.household_id not in (None, household.pk):
        raise DomainError("That type belongs to another household.")


def create_entity(household: Household, *, actor, data: dict, tags=(), location=None) -> Entity:
    data = dict(data)
    _check_type(household, data["entity_type"])
    data["attributes"] = validate_attributes(data.get("attributes"))
    with transaction.atomic():
        entity = Entity.objects.create(household=household, created_by=actor, updated_by=actor,
                                       **data)
        if tags:
            entity.tags.set(resolve_tags(household, tags))
        if location is not None:
            set_location(entity, location, actor=actor)
        audit.record(actor, "entity.create", entity)
    return entity


def ensure_editable(entity: Entity):
    if entity.is_archived:
        raise ArchivedError(f"'{entity.name}'")


def update_entity(entity: Entity, *, actor, expected_version: int, data: dict, tags=None) -> None:
    ensure_editable(entity)
    data = {k: v for k, v in data.items() if k in EDITABLE_FIELDS}
    if "entity_type" in data:
        _check_type(entity.household, data["entity_type"])
        new_kind = data["entity_type"].base_kind
        if new_kind == BaseKind.PROPERTY and location_of(entity) is not None:
            raise DomainError("A property is a top-level location; remove its location first.")
    if "attributes" in data:
        data["attributes"] = validate_attributes(data["attributes"])
    changes = audit.diff(entity, data, data.keys())
    with transaction.atomic():
        versioned_update(entity, expected_version, updated_by=actor,
                         updated_at=timezone.now(), **data)
        if tags is not None:
            old = sorted(t.name for t in entity.tags.all())
            new_tags = resolve_tags(entity.household, tags)
            entity.tags.set(new_tags)
            new = sorted(t.name for t in new_tags)
            if old != new:
                changes["tags"] = [old, new]
        audit.record(actor, "entity.update", entity, changes=changes)


def outstanding_work(entity: Entity) -> dict:
    from homefabric.maintenance.models import UNFINISHED, MaintenanceSchedule, Task

    return {
        "tasks": Task.objects.filter(entity=entity, status__in=UNFINISHED),
        "schedules": MaintenanceSchedule.objects.filter(entity=entity, enabled=True),
    }


def archive_entity(entity: Entity, *, actor, expected_version: int, outstanding: str | None,
                   reason: str = "") -> None:
    """Archive, requiring an explicit choice for outstanding work."""
    from homefabric.maintenance import services as maint
    from homefabric.maintenance.models import EventType

    if entity.is_archived:
        return
    work = outstanding_work(entity)
    has_work = work["tasks"].exists() or work["schedules"].exists()
    if has_work and outstanding not in ("retain", "cancel"):
        raise DomainError(
            "This item has open tasks or active schedules. Choose whether to keep or cancel "
            "that work before archiving."
        )
    with transaction.atomic():
        cancelled_tasks = disabled_schedules = 0
        if has_work and outstanding == "cancel":
            for task in work["tasks"]:
                maint.cancel_task(task, actor=actor, reason=f"Cancelled because '{entity.name}' "
                                  "was archived", allow_archived=True)
                cancelled_tasks += 1
            for schedule in work["schedules"]:
                maint.set_schedule_enabled(schedule, False, actor=actor, allow_archived=True)
                disabled_schedules += 1
        versioned_update(entity, expected_version, status=EntityStatus.ARCHIVED,
                         archived_at=timezone.now(), archived_by=actor, updated_by=actor)
        notes = reason
        if has_work:
            notes += (f"\n\nOutstanding work: {outstanding}. Cancelled {cancelled_tasks} task(s), "
                      f"disabled {disabled_schedules} schedule(s).")
        maint.record_system_activity(entity, EventType.ARCHIVED, actor=actor,
                                     title="Archived", notes=notes.strip())
        audit.record(actor, "entity.archive", entity,
                     changes={"outstanding": outstanding, "cancelled_tasks": cancelled_tasks,
                              "disabled_schedules": disabled_schedules})


def restore_entity(entity: Entity, *, actor, expected_version: int) -> None:
    from homefabric.maintenance import services as maint
    from homefabric.maintenance.models import EventType

    if not entity.is_archived:
        return
    with transaction.atomic():
        versioned_update(entity, expected_version, status=EntityStatus.ACTIVE, archived_at=None,
                         archived_by=None, updated_by=actor)
        maint.record_system_activity(entity, EventType.RESTORED, actor=actor, title="Restored")
        audit.record(actor, "entity.restore", entity)


# --- relationships --------------------------------------------------------

def _lock_household(household_id) -> None:
    # Serialises relationship writes per household on PostgreSQL. On SQLite
    # every write transaction already holds the database write lock because
    # transactions begin IMMEDIATE.
    list(Household.objects.select_for_update().filter(pk=household_id).values_list("pk"))


def location_of(entity: Entity) -> Entity | None:
    rel = EntityRelationship.objects.filter(
        source=entity, kind=RelationKind.LOCATED_IN
    ).select_related("target", "target__entity_type").first()
    return rel.target if rel else None


def location_path(entity: Entity) -> list[Entity]:
    """Ancestors from the top-level property down to the immediate location."""
    path = []
    seen = {entity.pk}
    current = location_of(entity)
    while current is not None and current.pk not in seen and len(path) < MAX_WALK:
        path.append(current)
        seen.add(current.pk)
        current = location_of(current)
    return list(reversed(path))


def _location_ancestor_ids(entity_id) -> set:
    ids = set()
    current = entity_id
    for _ in range(MAX_WALK):
        nxt = EntityRelationship.objects.filter(
            source_id=current, kind=RelationKind.LOCATED_IN
        ).values_list("target_id", flat=True).first()
        if nxt is None or nxt in ids:
            break
        ids.add(nxt)
        current = nxt
    return ids


def _part_of_reachable(start_id) -> set:
    """All entities reachable from ``start_id`` following part_of edges upward."""
    seen = set()
    frontier = [start_id]
    while frontier and len(seen) < MAX_WALK:
        parents = list(EntityRelationship.objects.filter(
            source_id__in=frontier, kind=RelationKind.PART_OF
        ).values_list("target_id", flat=True))
        frontier = [p for p in parents if p not in seen]
        seen.update(frontier)
    return seen


def _validate(source: Entity, target: Entity, kind: str) -> None:
    if source.household_id != target.household_id:
        raise DomainError("Relationships must stay within one household.")
    if source.pk == target.pk:
        raise DomainError("An item cannot be related to itself.")
    if source.is_archived or target.is_archived:
        raise ArchivedError("One of these items")
    s_kind, t_kind = source.entity_type.base_kind, target.entity_type.base_kind
    if kind == RelationKind.LOCATED_IN:
        if s_kind == BaseKind.PROPERTY:
            raise DomainError("A property is a top-level location and cannot be located in "
                              "anything.")
        if t_kind in COVER_KINDS:
            raise DomainError("Policies and agreements are not locations.")
        if s_kind in COVER_KINDS:
            raise DomainError("Policies and agreements cannot be placed in a location; use "
                              "'covers' instead.")
        if source.pk in _location_ancestor_ids(target.pk):
            raise DomainError(f"'{target.name}' is already inside '{source.name}'; this would "
                              "create a location loop.")
    elif kind == RelationKind.PART_OF:
        if s_kind in COVER_KINDS or t_kind in COVER_KINDS:
            raise DomainError("Policies and agreements use 'covers', not 'part of'.")
        if source.pk in _part_of_reachable(target.pk):
            raise DomainError(f"'{target.name}' is already part of '{source.name}'; this would "
                              "create a loop.")
    elif kind == RelationKind.COVERS:
        if s_kind not in COVER_KINDS:
            raise DomainError("Only policies and agreements can cover other items.")
        if t_kind not in (LOCATION_KINDS | ASSET_KINDS):
            raise DomainError("A policy or agreement can cover a property, location, or asset.")
    elif kind != RelationKind.ASSOCIATED:
        raise DomainError("Unknown relationship kind.")


def add_relationship(source: Entity, target: Entity, kind: str, *, actor,
                     label: str = "") -> EntityRelationship:
    try:
        with transaction.atomic():
            _lock_household(source.household_id)
            source = Entity.objects.select_related("entity_type").get(pk=source.pk)
            target = Entity.objects.select_related("entity_type").get(pk=target.pk)
            _validate(source, target, kind)
            if kind == RelationKind.ASSOCIATED:
                if str(source.pk) > str(target.pk):
                    source, target = target, source
                if EntityRelationship.objects.filter(
                    kind=kind, source__in=[source, target], target__in=[source, target]
                ).exists():
                    raise DomainError("These items are already associated.")
            if kind == RelationKind.LOCATED_IN and EntityRelationship.objects.filter(
                source=source, kind=kind
            ).exists():
                raise DomainError(f"'{source.name}' already has a location. Move it instead.")
            if EntityRelationship.objects.filter(source=source, target=target, kind=kind).exists():
                raise DomainError("That relationship already exists.")
            rel = EntityRelationship.objects.create(
                household_id=source.household_id, source=source, target=target, kind=kind,
                label=label.strip()[:120], created_by=actor,
            )
            audit.record(actor, "relationship.add", rel, label=str(rel))
            return rel
    except IntegrityError as exc:
        raise DomainError("That relationship conflicts with an existing one.") from exc


def remove_relationship(rel: EntityRelationship, *, actor) -> None:
    if rel.source.is_archived or rel.target.is_archived:
        raise ArchivedError("One of these items")
    label = str(rel)
    with transaction.atomic():
        audit.record(actor, "relationship.remove", rel, label=label)
        rel.delete()


def set_location(entity: Entity, location: Entity | None, *, actor) -> None:
    """Move ``entity`` to ``location`` (or clear it) atomically."""
    with transaction.atomic():
        _lock_household(entity.household_id)
        existing = EntityRelationship.objects.filter(source=entity, kind=RelationKind.LOCATED_IN)
        old = existing.select_related("target").first()
        if location is not None and old is not None and old.target_id == location.pk:
            return
        if entity.is_archived:
            raise ArchivedError(f"'{entity.name}'")
        if old is not None:
            existing.delete()
        if location is not None:
            add_relationship(entity, location, RelationKind.LOCATED_IN, actor=actor)
        audit.record(actor, "entity.move", entity, changes={
            "location": [old.target.name if old else None, location.name if location else None]
        })


def part_of_targets(entity: Entity):
    return Entity.objects.filter(incoming__source=entity, incoming__kind=RelationKind.PART_OF)


def set_part_of(entity: Entity, targets, *, actor, manageable=None) -> dict:
    """Make ``entity`` part of exactly ``targets``.

    Only memberships whose target is in ``manageable`` (IDs) may be removed,
    so links the caller could not offer, such as to archived systems, are kept.
    All changes apply together or not at all.
    """
    wanted = {t.pk: t for t in targets}
    added, removed = [], []
    with transaction.atomic():
        current = {r.target_id: r for r in EntityRelationship.objects.filter(
            source=entity, kind=RelationKind.PART_OF).select_related("source", "target")}
        for target_id, rel in current.items():
            if target_id not in wanted and (manageable is None or target_id in manageable):
                removed.append(rel.target.name)
                remove_relationship(rel, actor=actor)
        for target_id, target in wanted.items():
            if target_id not in current:
                add_relationship(entity, target, RelationKind.PART_OF, actor=actor)
                added.append(target.name)
    return {"added": added, "removed": removed}


def contents(location: Entity, include_archived: bool = False):
    qs = Entity.objects.filter(
        outgoing__kind=RelationKind.LOCATED_IN, outgoing__target=location
    ).select_related("entity_type")
    if not include_archived:
        qs = qs.filter(status=EntityStatus.ACTIVE)
    return qs.order_by(*SIBLING_ORDER)


def components(entity: Entity, include_archived: bool = False):
    qs = Entity.objects.filter(
        outgoing__kind=RelationKind.PART_OF, outgoing__target=entity
    ).select_related("entity_type")
    if not include_archived:
        qs = qs.filter(status=EntityStatus.ACTIVE)
    return qs.order_by(*SIBLING_ORDER)


def relationships_for(entity: Entity) -> dict:
    out = EntityRelationship.objects.filter(source=entity).select_related(
        "target", "target__entity_type")
    inc = EntityRelationship.objects.filter(target=entity).select_related(
        "source", "source__entity_type")
    return {
        "located_in": [r for r in out if r.kind == RelationKind.LOCATED_IN],
        "part_of": [r for r in out if r.kind == RelationKind.PART_OF],
        "associated": [(r, r.target) for r in out if r.kind == RelationKind.ASSOCIATED]
        + [(r, r.source) for r in inc if r.kind == RelationKind.ASSOCIATED],
        "covers": [r for r in out if r.kind == RelationKind.COVERS],
        "covered_by": [r for r in inc if r.kind == RelationKind.COVERS],
    }


def top_level_locations(household: Household, include_archived=False):
    qs = Entity.objects.filter(household=household).exclude(
        outgoing__kind=RelationKind.LOCATED_IN
    ).filter(entity_type__base_kind__in=list(LOCATION_KINDS)).select_related("entity_type")
    if not include_archived:
        qs = qs.filter(status=EntityStatus.ACTIVE)
    return qs.order_by(*SIBLING_ORDER)


def unplaced_entities(household: Household):
    return Entity.objects.filter(household=household, status=EntityStatus.ACTIVE).exclude(
        outgoing__kind=RelationKind.LOCATED_IN
    ).exclude(entity_type__base_kind__in=list(LOCATION_KINDS | COVER_KINDS)).select_related(
        "entity_type").order_by("name")


def top_level_systems(household: Household, include_archived=False):
    qs = Entity.objects.filter(
        household=household, entity_type__base_kind=BaseKind.SYSTEM
    ).exclude(outgoing__kind=RelationKind.PART_OF).select_related("entity_type")
    if not include_archived:
        qs = qs.filter(status=EntityStatus.ACTIVE)
    return qs.order_by(*SIBLING_ORDER)


def child_count_map(entity_ids, kind: str) -> dict:
    from django.db.models import Count

    rows = EntityRelationship.objects.filter(
        target_id__in=entity_ids, kind=kind, source__status=EntityStatus.ACTIVE
    ).values("target_id").annotate(n=Count("id"))
    return {r["target_id"]: r["n"] for r in rows}
