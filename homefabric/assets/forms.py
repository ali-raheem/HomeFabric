from django import forms
from django.db.models import Case, When

from homefabric.assets import services
from homefabric.assets.models import BaseKind, Entity, EntityStatus, EntityType, RelationKind
from homefabric.core import money
from homefabric.core.errors import DomainError
from homefabric.core.forms import DateInput, EntityChoiceField, VersionField


class EntityForm(forms.Form):
    name = forms.CharField(max_length=200)
    entity_type = forms.ModelChoiceField(queryset=EntityType.objects.none(), label="Type")
    location = EntityChoiceField(queryset=Entity.objects.none(), required=False,
                                 label="Located in", help_text="Where this item physically is.")
    part_of = forms.ModelMultipleChoiceField(
        queryset=Entity.objects.none(), required=False, label="Part of",
        help_text="Systems or equipment this item belongs to, e.g. Central heating and Hot "
                  "water. Choose as many as apply.")
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False,
                                  help_text="Markdown is supported.")
    tags = forms.CharField(required=False, help_text="Comma-separated, e.g. heating, annual.")
    sort_order = forms.IntegerField(
        required=False, min_value=0, max_value=100000, label="Sort position",
        help_text="Optional. Orders this item among others in the same place, lowest first "
                  "(e.g. Ground floor 1, First floor 2). Items without a position follow.")
    manufacturer = forms.CharField(max_length=150, required=False)
    model_number = forms.CharField(max_length=150, required=False, label="Model")
    serial_number = forms.CharField(max_length=150, required=False, label="Serial number")
    purchase_date = forms.DateField(required=False, widget=DateInput())
    installation_date = forms.DateField(required=False, widget=DateInput())
    warranty_end = forms.DateField(required=False, widget=DateInput(), label="Warranty ends")
    cost = forms.DecimalField(required=False, min_value=0, max_digits=14, decimal_places=3)
    cost_currency = forms.CharField(required=False, max_length=3, label="Currency")
    provider = forms.CharField(max_length=150, required=False, label="Provider / insurer / lender")
    reference_number = forms.CharField(max_length=150, required=False,
                                       label="Policy or reference number")
    start_date = forms.DateField(required=False, widget=DateInput())
    end_date = forms.DateField(required=False, widget=DateInput(), label="Expiry / end date")
    renewal_date = forms.DateField(required=False, widget=DateInput(),
                                   label="Renewal or review date")
    attributes = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Other details",
        help_text="One per line as 'name: value', e.g. 'Filter size: 22mm'.")
    version = VersionField(required=False)

    def __init__(self, *args, household, entity=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.household = household
        self.fields["entity_type"].queryset = services.types_for(household)
        locations = Entity.objects.filter(
            household=household, status=EntityStatus.ACTIVE
        ).exclude(entity_type__base_kind__in=[BaseKind.POLICY, BaseKind.AGREEMENT]).select_related(
            "entity_type").order_by("name")
        if entity is not None:
            locations = locations.exclude(pk=entity.pk)
        self.fields["location"].queryset = locations
        # Things an item can belong to: systems first, then equipment and components.
        parents = Entity.objects.filter(
            household=household, status=EntityStatus.ACTIVE,
            entity_type__base_kind__in=[BaseKind.SYSTEM, BaseKind.EQUIPMENT, BaseKind.COMPONENT],
        ).select_related("entity_type").order_by(
            Case(When(entity_type__base_kind=BaseKind.SYSTEM, then=0), default=1), "name")
        if entity is not None:
            parents = parents.exclude(pk=entity.pk)
        field = self.fields["part_of"]
        # Set the widget before the queryset: assigning the queryset fills the widget's choices.
        if parents.count() <= 30:
            field.widget = forms.CheckboxSelectMultiple()
        else:
            field.widget = forms.SelectMultiple(attrs={"size": 8})
            field.help_text += " Hold Ctrl (Cmd on a Mac) to select several."
        field.label_from_instance = lambda e: f"{e.name} ({e.entity_type.name})"
        field.queryset = parents
        if not self.is_bound:
            self.fields["cost_currency"].initial = household.currency

    def clean_cost_currency(self):
        value = (self.cleaned_data.get("cost_currency") or "").strip().upper()
        if value and (len(value) != 3 or not value.isalpha()):
            raise forms.ValidationError("Use a three-letter currency code such as GBP.")
        return value

    def clean_attributes(self):
        try:
            return services.parse_attribute_lines(self.cleaned_data.get("attributes", ""))
        except DomainError as exc:
            raise forms.ValidationError(str(exc)) from exc

    def entity_data(self) -> dict:
        d = self.cleaned_data
        currency = d.get("cost_currency") or self.household.currency
        cost_minor = money.to_minor(d["cost"], currency) if d.get("cost") is not None else None
        return {
            "name": d["name"].strip(), "entity_type": d["entity_type"],
            "description": d.get("description", ""),
            "manufacturer": d.get("manufacturer", ""), "model_number": d.get("model_number", ""),
            "serial_number": d.get("serial_number", ""), "purchase_date": d.get("purchase_date"),
            "installation_date": d.get("installation_date"),
            "warranty_end": d.get("warranty_end"), "cost_minor": cost_minor,
            "cost_currency": currency if cost_minor is not None else "",
            "provider": d.get("provider", ""), "reference_number": d.get("reference_number", ""),
            "start_date": d.get("start_date"), "end_date": d.get("end_date"),
            "renewal_date": d.get("renewal_date"), "attributes": d.get("attributes") or {},
            "sort_order": d.get("sort_order"),
        }

    @classmethod
    def initial_for(cls, entity: Entity) -> dict:
        initial = {f: getattr(entity, f) for f in (
            "name", "entity_type", "description", "manufacturer", "model_number",
            "serial_number", "purchase_date", "installation_date", "warranty_end", "provider",
            "reference_number", "start_date", "end_date", "renewal_date", "sort_order")}
        initial["tags"] = ", ".join(t.name for t in entity.tags.all())
        initial["attributes"] = services.format_attribute_lines(entity.attributes)
        initial["version"] = entity.version
        initial["location"] = services.location_of(entity)
        initial["part_of"] = list(services.part_of_targets(entity))
        if entity.cost_minor is not None:
            initial["cost"] = money.from_minor(entity.cost_minor, entity.cost_currency)
            initial["cost_currency"] = entity.cost_currency
        return initial


class RelationshipForm(forms.Form):
    kind = forms.ChoiceField(choices=[
        (RelationKind.PART_OF, "Part of (functional membership)"),
        (RelationKind.ASSOCIATED, "Associated with"),
        (RelationKind.COVERS, "Covers / applies to (policies and agreements)"),
    ])
    direction = forms.ChoiceField(
        choices=[("out", "This item → selected item"), ("in", "Selected item → this item")],
        initial="out", widget=forms.RadioSelect,
        help_text="'Part of': choose 'This item → selected' if this item is part of the "
                  "selected one.")
    other = EntityChoiceField(queryset=Entity.objects.none(), label="Related item")
    label = forms.CharField(max_length=120, required=False, help_text="Optional note.")

    def __init__(self, *args, entity, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["other"].queryset = Entity.objects.filter(
            household=entity.household_id, status=EntityStatus.ACTIVE
        ).exclude(pk=entity.pk).select_related("entity_type").order_by("name")


class MoveForm(forms.Form):
    location = EntityChoiceField(queryset=Entity.objects.none(), required=False,
                                 label="New location", help_text="Leave empty for no location.")
    version = VersionField()

    def __init__(self, *args, entity, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = Entity.objects.filter(
            household=entity.household_id, status=EntityStatus.ACTIVE
        ).exclude(pk=entity.pk).exclude(
            entity_type__base_kind__in=[BaseKind.POLICY, BaseKind.AGREEMENT]
        ).select_related("entity_type").order_by("name")


class ArchiveForm(forms.Form):
    outstanding = forms.ChoiceField(
        required=False, widget=forms.RadioSelect, label="Outstanding work",
        choices=[("retain", "Keep open tasks and active schedules as they are"),
                 ("cancel", "Cancel open tasks and disable schedules")])
    reason = forms.CharField(required=False, max_length=300, label="Reason (optional)")
    version = VersionField()


class EntityTypeForm(forms.Form):
    name = forms.CharField(max_length=80, help_text="For example 'Boiler' or 'Smoke alarm'.")
    base_kind = forms.ChoiceField(choices=BaseKind.choices, label="Behaves as")
    description = forms.CharField(max_length=300, required=False)


class EntityFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Filter by name")
    kind = forms.ChoiceField(required=False, choices=[("", "All kinds"), *BaseKind.choices])
    tag = forms.CharField(required=False)
    status = forms.ChoiceField(required=False, initial="active", choices=[
        ("active", "Active"), ("archived", "Archived"), ("all", "All")])
