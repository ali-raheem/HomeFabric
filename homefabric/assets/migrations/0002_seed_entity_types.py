from django.db import migrations

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


def seed(apps, schema_editor):
    EntityType = apps.get_model("assets", "EntityType")
    for name, kind, description in SEED_TYPES:
        EntityType.objects.get_or_create(
            household=None, name=name, defaults={"base_kind": kind, "description": description}
        )


class Migration(migrations.Migration):
    dependencies = [("assets", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
