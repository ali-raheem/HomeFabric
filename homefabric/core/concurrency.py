from django.db.models import F

from homefabric.core.errors import ConcurrencyError


def versioned_update(instance, expected_version: int, **fields) -> None:
    """Persist ``fields`` only if the row still has ``expected_version``.

    Uses a single conditional UPDATE so two users editing the same record
    cannot silently overwrite each other.
    """
    model = type(instance)
    updated = model.objects.filter(pk=instance.pk, version=expected_version).update(
        version=F("version") + 1, **fields
    )
    if updated != 1:
        raise ConcurrencyError()
    for key, value in fields.items():
        setattr(instance, key, value)
    instance.version = expected_version + 1
