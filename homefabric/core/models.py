import uuid

from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class HouseholdOwned(models.Model):
    """Every household-owned record carries its household for scoping."""

    household = models.ForeignKey(
        "accounts.Household", on_delete=models.PROTECT, related_name="+", db_index=True
    )

    class Meta:
        abstract = True


class Versioned(models.Model):
    """Optimistic concurrency: edits must supply the version they were based on."""

    version = models.PositiveIntegerField(default=1)

    class Meta:
        abstract = True


class AuditEvent(models.Model):
    """Administrative audit log; distinct from service history."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(
        "accounts.Household", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_label = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=80, db_index=True)
    object_type = models.CharField(max_length=80, blank=True)
    object_id = models.CharField(max_length=64, blank=True, db_index=True)
    object_label = models.CharField(max_length=300, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.action} {self.object_label}"
