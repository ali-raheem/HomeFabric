SEED_TYPES = [
    ("Property", "property", "A house, flat, or other top-level location"),
    ("Floor", "floor", "A storey or level within a property"),
    ("Room", "room", "A room or space"),
    ("System", "system", "A functional system such as central heating or electrics"),
    ("Equipment", "equipment", "An appliance, device, or installed item"),
    ("Component", "component", "A part of a system or piece of equipment"),
    ("Policy", "policy", "An insurance policy or warranty"),
    ("Agreement", "agreement", "A mortgage, contract, or service agreement"),
]


def ensure_seed_types(entity_type_model=None) -> None:
    if entity_type_model is None:
        from homefabric.assets.models import EntityType as entity_type_model
    if entity_type_model.objects.filter(household__isnull=True).count() >= len(SEED_TYPES):
        return
    for name, kind, description in SEED_TYPES:
        entity_type_model.objects.get_or_create(
            household=None, name=name, defaults={"base_kind": kind, "description": description})
