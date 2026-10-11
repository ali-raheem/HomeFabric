import uuid

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower

from homefabric.core.models import HouseholdOwned, TimeStampedModel, Versioned


class BaseKind(models.TextChoices):
    PROPERTY = "property", "Property"
    FLOOR = "floor", "Floor"
    ROOM = "room", "Room"
    SYSTEM = "system", "System"
    EQUIPMENT = "equipment", "Equipment"
    COMPONENT = "component", "Component"
    POLICY = "policy", "Policy"
    AGREEMENT = "agreement", "Agreement"


LOCATION_KINDS = {BaseKind.PROPERTY, BaseKind.FLOOR, BaseKind.ROOM}
ASSET_KINDS = {BaseKind.SYSTEM, BaseKind.EQUIPMENT, BaseKind.COMPONENT}
COVER_KINDS = {BaseKind.POLICY, BaseKind.AGREEMENT}


class EntityType(models.Model):
    """Seed types (household is null) plus owner-defined labels.

    Owner-defined types map onto a base kind, so a "Boiler" type behaves as
    equipment without a dedicated table.
    """

    household = models.ForeignKey(
        "accounts.Household", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    name = models.CharField(max_length=80)
    base_kind = models.CharField(max_length=20, choices=BaseKind.choices)
    description = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                "household", Lower("name"), name="unique_entity_type_name_per_household"
            )
        ]

    def __str__(self):
        return self.name

    @property
    def is_seed(self) -> bool:
        return self.household_id is None


class Tag(HouseholdOwned):
    name = models.CharField(max_length=60)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint("household", Lower("name"), name="unique_tag_per_household")
        ]

    def __str__(self):
        return self.name


class EntityStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class Entity(HouseholdOwned, TimeStampedModel, Versioned):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity_type = models.ForeignKey(EntityType, on_delete=models.PROTECT, related_name="entities")
    name = models.CharField(max_length=200, db_index=True)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=10, choices=EntityStatus.choices, default=EntityStatus.ACTIVE, db_index=True
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="entities")

    # Equipment details (query-critical, typed columns)
    manufacturer = models.CharField(max_length=150, blank=True)
    model_number = models.CharField(max_length=150, blank=True, db_index=True)
    serial_number = models.CharField(max_length=150, blank=True, db_index=True)
    purchase_date = models.DateField(null=True, blank=True)
    installation_date = models.DateField(null=True, blank=True)
    warranty_end = models.DateField(null=True, blank=True, db_index=True)
    cost_minor = models.BigIntegerField(null=True, blank=True)
    cost_currency = models.CharField(max_length=3, blank=True)

    # Policy / agreement details
    provider = models.CharField(max_length=150, blank=True)
    reference_number = models.CharField(max_length=150, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True, db_index=True)
    renewal_date = models.DateField(null=True, blank=True, db_index=True)

    # Less common details, validated against a small schema in services.
    attributes = models.JSONField(default=dict, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "entities"
        indexes = [models.Index(fields=["household", "status", "name"])]

    def __str__(self):
        return self.name

    @property
    def kind(self) -> str:
        return self.entity_type.base_kind

    @property
    def is_archived(self) -> bool:
        return self.status == EntityStatus.ARCHIVED

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("assets:entity_detail", args=[self.pk])


class RelationKind(models.TextChoices):
    LOCATED_IN = "located_in", "Located in"
    PART_OF = "part_of", "Part of"
    ASSOCIATED = "associated", "Associated with"
    COVERS = "covers", "Covers / applies to"


class EntityRelationship(HouseholdOwned):
    """Directed edge source -> target.

    located_in: source is located in target (one per source, acyclic).
    part_of:    source is part of target (many parents, acyclic).
    associated: symmetric; stored once with source.id < target.id.
    covers:     source (policy/agreement) covers target.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="outgoing")
    target = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="incoming")
    kind = models.CharField(max_length=20, choices=RelationKind.choices, db_index=True)
    label = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source", "target", "kind"], name="unique_relationship"),
            models.UniqueConstraint(
                fields=["source"], condition=models.Q(kind="located_in"),
                name="one_location_per_entity",
            ),
            models.CheckConstraint(
                condition=~models.Q(source=models.F("target")), name="no_self_relationship"
            ),
        ]
        indexes = [models.Index(fields=["target", "kind"]), models.Index(fields=["source", "kind"])]

    def __str__(self):
        return f"{self.source} {self.get_kind_display().lower()} {self.target}"
