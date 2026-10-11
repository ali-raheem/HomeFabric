import uuid

from django.conf import settings
from django.db import models

from homefabric.core.models import HouseholdOwned, Versioned


class StoredBlob(HouseholdOwned):
    """Content-addressed file within one household.

    Identical content is stored once per household (never shared across
    households). ``storage_key`` is an opaque relative path under BLOB_DIR.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sha256 = models.CharField(max_length=64)
    size = models.BigIntegerField()
    storage_key = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["household", "sha256"], name="unique_blob_per_household")
        ]


class DocumentCategory(models.TextChoices):
    MANUAL = "manual", "Manual"
    INVOICE = "invoice", "Invoice / receipt"
    WARRANTY = "warranty", "Warranty"
    POLICY = "policy", "Policy / agreement"
    CERTIFICATE = "certificate", "Certificate"
    PHOTO = "photo", "Photo"
    OTHER = "other", "Other"


class Document(HouseholdOwned, Versioned):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_name = models.CharField(max_length=200, db_index=True)
    description = models.TextField(blank=True)
    category = models.CharField(
        max_length=20, choices=DocumentCategory.choices, default=DocumentCategory.OTHER
    )
    current_revision = models.ForeignKey(
        "DocumentRevision", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name"]

    def __str__(self):
        return self.display_name

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("documents:detail", args=[self.pk])


class DocumentRevision(models.Model):
    """Immutable file revision. Replacing a document adds a revision."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="revisions")
    revision_number = models.PositiveIntegerField()
    blob = models.ForeignKey(StoredBlob, on_delete=models.PROTECT, related_name="revisions")
    original_filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=120)
    size = models.BigIntegerField()
    sha256 = models.CharField(max_length=64)
    note = models.CharField(max_length=300, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-revision_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "revision_number"], name="unique_revision_number"
            )
        ]

    def __str__(self):
        return f"{self.document} r{self.revision_number}"


class EntityDocument(HouseholdOwned):
    """Attach a document (always its current revision) to an entity."""

    entity = models.ForeignKey("assets.Entity", on_delete=models.CASCADE, related_name="document_links")
    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="entity_links")
    attached_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    attached_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["entity", "document"], name="unique_entity_document")
        ]


class ActivityDocument(HouseholdOwned):
    """Evidence for a history entry, pinned to the exact revision attached."""

    activity = models.ForeignKey(
        "maintenance.ActivityEntry", on_delete=models.PROTECT, related_name="document_links"
    )
    revision = models.ForeignKey(DocumentRevision, on_delete=models.PROTECT, related_name="+")
    attached_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    attached_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["activity", "revision"], name="unique_activity_document")
        ]


class TaskDocument(HouseholdOwned):
    task = models.ForeignKey("maintenance.Task", on_delete=models.CASCADE, related_name="document_links")
    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="task_links")
    attached_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    attached_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["task", "document"], name="unique_task_document")
        ]
