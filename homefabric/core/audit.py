from homefabric.core.models import AuditEvent
from homefabric.core.request_context import get_request_id


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def record(actor, action: str, obj=None, *, household=None, changes=None, label: str = ""):
    """Write an audit event. ``changes`` maps field -> [old, new]."""
    if household is None and obj is not None:
        household = getattr(obj, "household", None)
    return AuditEvent.objects.create(
        household=household,
        actor=actor if getattr(actor, "pk", None) else None,
        actor_label=(getattr(actor, "username", "") or "system")[:150],
        action=action,
        object_type=obj._meta.label if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        object_label=(label or (str(obj) if obj is not None else ""))[:300],
        changes=_jsonable(changes or {}),
        request_id=get_request_id(),
    )


def diff(instance, data: dict, fields) -> dict:
    """Return {field: [old, new]} for fields whose value changes."""
    out = {}
    for f in fields:
        if f not in data:
            continue
        old = getattr(instance, f)
        new = data[f]
        if hasattr(old, "pk"):
            old = old.pk
        if hasattr(new, "pk"):
            new = new.pk
        if old != new:
            out[f] = [old, new]
    return out
