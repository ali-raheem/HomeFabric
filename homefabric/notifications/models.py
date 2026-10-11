import os
import uuid

from django.conf import settings
from django.db import models

from homefabric.core.redaction import mask_secret_url


class NotificationEndpoint(models.Model):
    """A user's delivery target for one channel provider.

    The secret (e.g. an Apprise URL) is either stored in ``secret_value`` or
    referenced as an environment variable via ``secret_ref``. Stored secrets are
    stripped from backups; endpoints then need re-verification.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_endpoints"
    )
    provider = models.CharField(max_length=30)
    display_name = models.CharField(max_length=100)
    address = models.CharField(max_length=254, blank=True, help_text="Email address for email.")
    secret_value = models.TextField(blank=True)
    secret_ref = models.CharField(max_length=120, blank=True)
    enabled = models.BooleanField(default=True)
    needs_verification = models.BooleanField(default=False)
    deleted = models.BooleanField(default=False)
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_test_ok = models.BooleanField(null=True, blank=True)
    last_test_error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["provider", "display_name"]

    def __str__(self):
        return f"{self.display_name} ({self.provider})"

    def resolve_secret(self) -> str:
        if self.secret_ref:
            return os.environ.get(self.secret_ref, "")
        return self.secret_value

    @property
    def masked_target(self) -> str:
        if self.provider == "email":
            return self.address
        if self.secret_ref:
            return f"from environment variable {self.secret_ref}"
        return mask_secret_url(self.secret_value)

    @property
    def is_usable(self) -> bool:
        return self.enabled and not self.deleted and not self.needs_verification


class DeliveryKind(models.TextChoices):
    REMINDER = "reminder", "Reminder"
    CATCHUP = "catchup", "Catch-up summary"
    TEST = "test", "Test message"
    FAILURE_ALERT = "failure_alert", "Delivery failure alert"


class DeliveryState(models.TextChoices):
    PENDING = "pending", "Pending"
    LEASED = "leased", "Sending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    SUPPRESSED = "suppressed", "Suppressed"
    BATCHED = "batched", "Included in summary"
    REVIEW = "review", "Needs review"


ACTIVE_STATES = [DeliveryState.PENDING, DeliveryState.LEASED]


class NotificationDelivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(
        "accounts.Household", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    kind = models.CharField(max_length=20, choices=DeliveryKind.choices)
    event_key = models.CharField(max_length=200)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="deliveries"
    )
    endpoint = models.ForeignKey(
        NotificationEndpoint, on_delete=models.PROTECT, related_name="deliveries"
    )
    channel = models.CharField(max_length=30)
    task = models.ForeignKey(
        "maintenance.Task", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    batch = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="batched_items"
    )
    subject = models.CharField(max_length=250)
    body = models.TextField()
    due_at = models.DateTimeField(db_index=True)
    state = models.CharField(
        max_length=12, choices=DeliveryState.choices, default=DeliveryState.PENDING, db_index=True
    )
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(db_index=True)
    lease_owner = models.CharField(max_length=64, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=1000, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    acknowledged = models.BooleanField(default=False)

    class Meta:
        ordering = ["-due_at"]
        verbose_name_plural = "notification deliveries"
        constraints = [
            models.UniqueConstraint(
                fields=["event_key", "recipient", "endpoint"], name="unique_delivery_event"
            )
        ]
        indexes = [models.Index(fields=["state", "next_attempt_at"])]

    def __str__(self):
        return f"{self.get_kind_display()} to {self.recipient} via {self.channel}"
