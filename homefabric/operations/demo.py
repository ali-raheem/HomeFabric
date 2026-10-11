"""The specification's boiler scenario, used for demos and design validation."""

import datetime as dt

from homefabric.assets import services as assets
from homefabric.assets.models import EntityType, RelationKind
from homefabric.core.dates import household_today
from homefabric.maintenance import services as maint
from homefabric.maintenance.models import EventType, IntervalUnit, RecurrenceMode, TaskKind


def _type(kind):
    from homefabric.assets.seed import ensure_seed_types

    ensure_seed_types()
    return EntityType.objects.get(household__isnull=True, base_kind=kind)


def build(household, user) -> dict:
    today = household_today(household)

    def make(name, kind, location=None, **data):
        return assets.create_entity(household, actor=user, location=location,
                                    data={"name": name, "entity_type": _type(kind), **data})

    house = make("12 Example Road", "property", description="Three-bedroom semi-detached house.")
    ground = make("Ground floor", "floor", location=house)
    first = make("First floor", "floor", location=house)
    utility = make("Utility room", "room", location=ground)
    bedroom = make("Bedroom 1", "room", location=first)
    heating = make("Central heating", "system", description="Gas combi boiler, 9 radiators.")
    boiler = make("Boiler", "equipment", location=utility, manufacturer="Worcester Bosch",
                  model_number="Greenstar 30i", serial_number="WB30I-0001234",
                  installation_date=today - dt.timedelta(days=3 * 365),
                  warranty_end=today + dt.timedelta(days=2 * 365))
    filt = make("MagnaClean Professional 2", "component", location=utility,
                manufacturer="Adey", model_number="MagnaClean Professional 2",
                attributes={"Pipe size": "22mm"})
    radiator = make("Bedroom 1 radiator", "equipment", location=bedroom,
                    attributes={"Valve": "TRV"})
    for item in (boiler, filt, radiator):
        assets.add_relationship(item, heating, RelationKind.PART_OF, actor=user)
    assets.add_relationship(boiler, filt, RelationKind.ASSOCIATED, actor=user,
                            label="Magnetic filter on boiler return")
    policy = make("Home insurance", "policy", provider="Example Insurance Ltd",
                  reference_number="HI-998877", start_date=today - dt.timedelta(days=300),
                  end_date=today + dt.timedelta(days=65), renewal_date=today + dt.timedelta(days=40))
    assets.add_relationship(policy, house, RelationKind.COVERS, actor=user)
    mortgage = make("Mortgage", "agreement", provider="Example Bank",
                    reference_number="MTG-12345", renewal_date=today + dt.timedelta(days=200),
                    description="Fixed rate ends; review options three months before.")
    assets.add_relationship(mortgage, house, RelationKind.COVERS, actor=user)

    service = maint.create_schedule(
        boiler, actor=user, title="Annual boiler service", mode=RecurrenceMode.FIXED,
        interval_count=1, interval_unit=IntervalUnit.YEAR,
        anchor_date=today + dt.timedelta(days=30))
    first_service = service.tasks.order_by("due_date").first()
    maint.create_task(boiler, actor=user, title="Book boiler service engineer",
                      kind=TaskKind.BOOKING, related_task=first_service,
                      due_date=today + dt.timedelta(days=7))
    maint.create_schedule(
        filt, actor=user, title="Clean MagnaClean filter", mode=RecurrenceMode.AFTER_COMPLETION,
        interval_count=6, interval_unit=IntervalUnit.MONTH, anchor_date=today + dt.timedelta(days=14))
    maint.create_schedule(
        policy, actor=user, title="Review insurance renewal", mode=RecurrenceMode.FIXED,
        interval_count=1, interval_unit=IntervalUnit.YEAR,
        anchor_date=policy.renewal_date - dt.timedelta(days=21))
    obs = maint.add_activity(radiator, actor=user, event_type=EventType.OBSERVATION,
                             occurred_on=today, title="Cold at the top",
                             notes="Top third of the radiator is cold; probably needs bleeding.")
    maint.create_task(radiator, actor=user, title="Bleed bedroom radiator",
                      due_date=today + dt.timedelta(days=3), source_activity=obs)
    maint.add_activity(boiler, actor=user, event_type=EventType.SERVICE,
                       occurred_on=today - dt.timedelta(days=330), title="Annual service",
                       provider="Gas Safe engineer", amount="95.00", currency=household.currency,
                       notes="Flue gas analysis OK. Pressure topped up to 1.3 bar.")
    return {"property": house, "boiler": boiler, "heating": heating, "radiator": radiator}
