"""Entities and relationships: A01, A02, A11 and relationship rules (spec §5)."""

import re
import threading

import pytest
from django.db import connection
from django.urls import reverse

from homefabric.assets import services
from homefabric.assets.models import Entity, EntityRelationship, EntityStatus, RelationKind
from homefabric.core.errors import ArchivedError, ConcurrencyError, DomainError
from homefabric.maintenance import services as maint
from homefabric.maintenance.models import MaintenanceSchedule, Task, TaskStatus
from tests.conftest import login, seed_type

pytestmark = pytest.mark.django_db


def test_a01_same_radiator_under_bedroom_and_heating(home, owner, client):
    radiator, heating, bedroom = home["radiator"], home["heating"], home["bedroom"]
    services.add_relationship(radiator, heating, RelationKind.PART_OF, actor=owner)
    assert list(services.contents(bedroom)) == [radiator]
    assert list(services.components(heating)) == [radiator]
    assert Entity.objects.filter(name="Bedroom radiator").count() == 1
    login(client, owner)
    by_location = client.get(reverse("assets:explorer_children", args=[bedroom.pk]),
                             {"mode": "location"})
    by_system = client.get(reverse("assets:explorer_children", args=[heating.pk]),
                           {"mode": "system"})
    url = radiator.get_absolute_url()
    assert url in by_location.content.decode() and url in by_system.content.decode()


def test_a01_boiler_exposes_associated_equipment_without_merging(home, owner):
    services.add_relationship(home["boiler"], home["filter"], RelationKind.ASSOCIATED, actor=owner)
    maint.add_activity(home["boiler"], actor=owner, event_type="service",
                       occurred_on=__import__("datetime").date.today(), title="Boiler service")
    rels = services.relationships_for(home["boiler"])
    assert [other for _, other in rels["associated"]] == [home["filter"]]
    assert home["filter"].activity.count() == 0
    reverse_rels = services.relationships_for(home["filter"])
    assert [other for _, other in reverse_rels["associated"]] == [home["boiler"]]


def test_location_path_supports_ten_levels(household, make_entity):
    parent = make_entity(household, "Estate", "property")
    chain = [parent]
    for i in range(11):
        parent = make_entity(household, f"Level {i}", "room", location=parent)
        chain.append(parent)
    assert services.location_path(parent) == chain[:-1]


def test_a02_containment_cycle_rejected_without_partial_write(home, owner):
    utility, house = home["utility"], home["house"]
    before = EntityRelationship.objects.count()
    with pytest.raises(DomainError):
        services.set_location(house, utility, actor=owner)  # a property is top-level
    with pytest.raises(DomainError, match="loop"):
        _make_cycle(home, owner)
    assert EntityRelationship.objects.count() == before
    assert services.location_of(utility) == house


def _make_cycle(home, owner):
    # Boiler is in the utility room; moving the utility room into the boiler is a cycle.
    services.set_location(home["utility"], home["boiler"], actor=owner)


def test_a02_part_of_cycle_rejected(home, owner):
    services.add_relationship(home["filter"], home["boiler"], RelationKind.PART_OF, actor=owner)
    services.add_relationship(home["boiler"], home["heating"], RelationKind.PART_OF, actor=owner)
    with pytest.raises(DomainError, match="loop"):
        services.add_relationship(home["heating"], home["filter"], RelationKind.PART_OF,
                                  actor=owner)


def test_part_of_allows_multiple_parents(home, owner, household, make_entity):
    plumbing = make_entity(household, "Plumbing", "system")
    services.add_relationship(home["radiator"], home["heating"], RelationKind.PART_OF, actor=owner)
    services.add_relationship(home["radiator"], plumbing, RelationKind.PART_OF, actor=owner)
    assert len(services.relationships_for(home["radiator"])["part_of"]) == 2


def test_a02_cross_household_relationship_rejected(home, owner, other_household, make_entity,
                                                   other_owner):
    foreign = make_entity(other_household, "Their boiler", actor=other_owner)
    before = EntityRelationship.objects.count()
    with pytest.raises(DomainError, match="one household"):
        services.add_relationship(home["boiler"], foreign, RelationKind.ASSOCIATED, actor=owner)
    with pytest.raises(DomainError, match="one household"):
        services.set_location(home["boiler"], foreign, actor=owner)
    assert EntityRelationship.objects.count() == before
    assert services.location_of(home["boiler"]) == home["utility"]


def test_associated_symmetric_no_duplicates_or_self(home, owner):
    services.add_relationship(home["boiler"], home["filter"], RelationKind.ASSOCIATED, actor=owner)
    with pytest.raises(DomainError):
        services.add_relationship(home["filter"], home["boiler"], RelationKind.ASSOCIATED,
                                  actor=owner)
    with pytest.raises(DomainError):
        services.add_relationship(home["boiler"], home["boiler"], RelationKind.ASSOCIATED,
                                  actor=owner)
    assert EntityRelationship.objects.filter(kind=RelationKind.ASSOCIATED).count() == 1


def test_covers_direction_rules(home, owner, household, make_entity):
    policy = make_entity(household, "Insurance", "policy")
    services.add_relationship(policy, home["house"], RelationKind.COVERS, actor=owner)
    with pytest.raises(DomainError):
        services.add_relationship(home["house"], policy, RelationKind.COVERS, actor=owner)
    assert services.relationships_for(home["house"])["covered_by"][0].source == policy


def test_one_immediate_location_enforced_by_database(home):
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        EntityRelationship.objects.create(household=home["boiler"].household,
                                          source=home["boiler"], target=home["bedroom"],
                                          kind=RelationKind.LOCATED_IN)


def test_optimistic_concurrency_prevents_silent_overwrite(home, owner, editor):
    boiler = home["boiler"]
    stale = Entity.objects.get(pk=boiler.pk)
    services.update_entity(boiler, actor=owner, expected_version=boiler.version,
                           data={"name": "Boiler (kitchen)"})
    with pytest.raises(ConcurrencyError):
        services.update_entity(stale, actor=editor, expected_version=stale.version,
                               data={"name": "Old boiler"})
    assert Entity.objects.get(pk=boiler.pk).name == "Boiler (kitchen)"


def test_attributes_validated(household, make_entity):
    with pytest.raises(DomainError):
        services.validate_attributes({"nested": {"a": 1}})
    assert services.parse_attribute_lines("Filter: 22mm\nColour: white") == {
        "Filter": "22mm", "Colour": "white"}
    e = make_entity(household, "Thing", attributes={"Size": "L"})
    assert e.attributes == {"Size": "L"}


def test_a11_archive_requires_explicit_choice_for_outstanding_work(home, owner, today):
    boiler = home["boiler"]
    task = maint.create_task(boiler, actor=owner, title="Fix leak", due_date=today)
    schedule = maint.create_schedule(boiler, actor=owner, title="Service", mode="fixed",
                                     interval_count=1, interval_unit="year", anchor_date=today)
    with pytest.raises(DomainError, match="Choose"):
        services.archive_entity(boiler, actor=owner, expected_version=boiler.version,
                                outstanding=None)
    services.archive_entity(boiler, actor=owner, expected_version=boiler.version,
                            outstanding="cancel", reason="Replaced")
    boiler.refresh_from_db()
    assert boiler.status == EntityStatus.ARCHIVED
    assert Task.objects.get(pk=task.pk).status == TaskStatus.CANCELLED
    assert not MaintenanceSchedule.objects.get(pk=schedule.pk).enabled
    assert boiler.activity.filter(event_type="archived").exists()
    assert boiler.activity.filter(event_type="task_cancelled").exists()
    # Related entities are not archived; history stays readable; edits blocked.
    assert Entity.objects.get(pk=home["utility"].pk).status == EntityStatus.ACTIVE
    with pytest.raises(ArchivedError):
        services.update_entity(boiler, actor=owner, expected_version=boiler.version,
                               data={"name": "x"})
    with pytest.raises(ArchivedError):
        maint.add_activity(boiler, actor=owner, event_type="note", occurred_on=today)


def test_a11_archive_retain_keeps_work_open(home, owner, today):
    radiator = home["radiator"]
    task = maint.create_task(radiator, actor=owner, title="Bleed", due_date=today)
    services.archive_entity(radiator, actor=owner, expected_version=radiator.version,
                            outstanding="retain")
    assert Task.objects.get(pk=task.pk).status == TaskStatus.OPEN
    radiator.refresh_from_db()
    services.restore_entity(radiator, actor=owner, expected_version=radiator.version)
    radiator.refresh_from_db()
    assert radiator.status == EntityStatus.ACTIVE


def test_archive_web_flow_shows_choice(home, owner, client, today):
    maint.create_task(home["boiler"], actor=owner, title="Fix", due_date=today)
    login(client, owner)
    url = reverse("assets:entity_archive", args=[home["boiler"].pk])
    page = client.get(url)
    assert b"outstanding work" in page.content
    r = client.post(url, {"version": home["boiler"].version})
    assert r.status_code == 200  # choice required
    r = client.post(url, {"version": home["boiler"].version, "outstanding": "retain"})
    assert r.status_code == 302


def test_entity_type_labels_without_new_tables(household, owner, make_entity):
    et = services.create_entity_type(household, name="Boiler", base_kind="equipment", actor=owner)
    e = services.create_entity(household, actor=owner, data={"name": "B", "entity_type": et})
    assert e.kind == "equipment"
    with pytest.raises(DomainError):
        services.create_entity_type(household, name="boiler", base_kind="equipment", actor=owner)


def test_entity_names_need_not_be_unique(household, make_entity):
    make_entity(household, "Radiator")
    make_entity(household, "Radiator")
    assert Entity.objects.filter(name="Radiator").count() == 2


@pytest.mark.django_db(transaction=True)
def test_concurrent_opposite_part_of_edges_cannot_both_succeed(household, make_entity, owner):
    if connection.vendor != "sqlite":
        pytest.skip("sqlite-specific")
    a = make_entity(household, "A", "system")
    b = make_entity(household, "B", "system")
    barrier = threading.Barrier(2)
    results = []

    def worker(src, dst):
        from django.db import connections

        barrier.wait()
        try:
            services.add_relationship(src, dst, RelationKind.PART_OF, actor=owner)
            results.append("ok")
        except DomainError:
            results.append("rejected")
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(a, b)),
               threading.Thread(target=worker, args=(b, a))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["ok", "rejected"]
    assert EntityRelationship.objects.filter(kind=RelationKind.PART_OF).count() == 1


def test_seed_types_exist(db):
    assert {seed_type(k).base_kind for k in ("property", "floor", "room", "system", "equipment",
                                              "component", "policy", "agreement")}


def test_sort_order_controls_sibling_order(household, owner, make_entity, client):
    house = make_entity(household, "House", "property")
    attic = make_entity(household, "Attic", "floor", location=house)
    first = make_entity(household, "First floor", "floor", location=house, sort_order=2)
    ground = make_entity(household, "Ground floor", "floor", location=house, sort_order=1)
    garden = make_entity(household, "Garden", "room", location=house)
    # Positioned items first by position; the rest by kind, then name.
    assert list(services.contents(house)) == [ground, first, attic, garden]
    login(client, owner)
    html = client.get(reverse("assets:explorer_children", args=[house.pk])).content.decode()
    assert html.index("Ground floor") < html.index("First floor") < html.index("Attic")


def test_sort_order_editable_via_form(household, owner, make_entity, client):
    house = make_entity(household, "House", "property")
    floor = make_entity(household, "Loft", "floor", location=house)
    login(client, owner)
    r = client.post(reverse("assets:entity_edit", args=[floor.pk]), {
        "name": "Loft", "entity_type": floor.entity_type_id, "location": house.pk,
        "sort_order": "3", "cost_currency": "GBP", "version": floor.version})
    assert r.status_code == 302
    floor.refresh_from_db()
    assert floor.sort_order == 3


def _form(entity_type, **extra):
    return {"entity_type": entity_type.pk, "cost_currency": "GBP", **extra}


def test_create_item_in_location_and_two_systems(home, household, owner, client, make_entity):
    hot_water = make_entity(household, "Hot water", "system")
    login(client, owner)
    page = client.get(reverse("assets:entity_create")).content.decode()
    assert 'name="part_of"' in page and "Hot water (System)" in page
    r = client.post(reverse("assets:entity_create"), _form(
        seed_type("equipment"), name="Combi boiler", location=home["utility"].pk,
        part_of=[home["heating"].pk, hot_water.pk]))
    assert r.status_code == 302
    boiler = Entity.objects.get(name="Combi boiler")
    assert services.location_of(boiler) == home["utility"]
    assert set(services.part_of_targets(boiler)) == {home["heating"], hot_water}


def test_edit_changes_memberships(home, household, owner, client, make_entity):
    hot_water = make_entity(household, "Hot water", "system")
    boiler = home["boiler"]
    services.add_relationship(boiler, home["heating"], RelationKind.PART_OF, actor=owner)
    login(client, owner)
    page = client.get(reverse("assets:entity_edit", args=[boiler.pk])).content.decode()
    assert re.search(rf'name="part_of" value="{home["heating"].pk}"[^>]*checked', page)
    r = client.post(reverse("assets:entity_edit", args=[boiler.pk]), _form(
        boiler.entity_type, name="Boiler", location=home["utility"].pk,
        part_of=[hot_water.pk], version=boiler.version))
    assert r.status_code == 302
    assert list(services.part_of_targets(boiler)) == [hot_water]


def test_membership_loop_rolls_back_whole_edit(home, household, owner, client):
    heating, boiler = home["heating"], home["boiler"]
    services.add_relationship(boiler, heating, RelationKind.PART_OF, actor=owner)
    login(client, owner)
    r = client.post(reverse("assets:entity_edit", args=[heating.pk]), _form(
        heating.entity_type, name="Renamed heating", part_of=[boiler.pk],
        version=heating.version))
    assert r.status_code == 200 and b"loop" in r.content
    heating.refresh_from_db()
    assert heating.name == "Central heating"  # nothing from the edit was saved
    assert not services.part_of_targets(heating).exists()


def test_archived_system_membership_preserved_on_edit(home, household, owner, client,
                                                      make_entity):
    old = make_entity(household, "Old heating", "system")
    radiator = home["radiator"]
    services.add_relationship(radiator, old, RelationKind.PART_OF, actor=owner)
    services.archive_entity(old, actor=owner, expected_version=old.version, outstanding=None)
    login(client, owner)
    r = client.post(reverse("assets:entity_edit", args=[radiator.pk]), _form(
        radiator.entity_type, name="Bedroom radiator", location=home["bedroom"].pk,
        part_of=[home["heating"].pk], version=radiator.version))
    assert r.status_code == 302
    assert set(services.part_of_targets(radiator)) == {old, home["heating"]}


def test_part_of_cannot_reference_other_household(home, owner, client, other_household,
                                                  other_owner, make_entity):
    foreign = make_entity(other_household, "Their heating", "system", actor=other_owner)
    login(client, owner)
    r = client.post(reverse("assets:entity_create"), _form(
        seed_type("equipment"), name="Sneaky", part_of=[foreign.pk]))
    assert r.status_code == 200 and not Entity.objects.filter(name="Sneaky").exists()


def test_new_component_link_preselects_system(home, owner, client):
    login(client, owner)
    page = client.get(reverse("assets:entity_create"), {"part_of": home["heating"].pk})
    html = page.content.decode()
    assert re.search(rf'name="part_of" value="{home["heating"].pk}"[^>]*checked', html)


def test_many_parents_use_compact_multi_select(household, owner, client, make_entity):
    for i in range(31):
        make_entity(household, f"System {i:02}", "system")
    login(client, owner)
    html = client.get(reverse("assets:entity_create")).content.decode()
    assert re.search(r'<select name="part_of"[^>]*multiple', html)
    assert "Hold Ctrl" in html
