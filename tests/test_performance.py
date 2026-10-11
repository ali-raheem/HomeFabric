"""Acceptance envelope (spec §15): 10,000 entities, 100,000 history entries.

Marked slow; run with ``pytest -m slow``. Builds data with bulk inserts, then
times the main pages server-side.
"""

import datetime as dt
import time
import uuid

import pytest
from django.test import Client

from homefabric.assets.models import Entity, EntityRelationship, RelationKind
from homefabric.documents.models import Document
from homefabric.maintenance.models import ActivityEntry, Task
from tests.conftest import login, seed_type

pytestmark = [pytest.mark.slow, pytest.mark.django_db]


def p95(samples):
    samples = sorted(samples)
    return samples[int(len(samples) * 0.95) - 1]


def test_envelope_response_times(household, owner):
    room_t, eq_t, prop_t = seed_type("room"), seed_type("equipment"), seed_type("property")
    house = Entity.objects.create(household=household, entity_type=prop_t, name="House")
    rooms = [Entity(household=household, entity_type=room_t, name=f"Room {i}") for i in range(100)]
    Entity.objects.bulk_create(rooms)
    EntityRelationship.objects.bulk_create([
        EntityRelationship(household=household, source=r, target=house, kind=RelationKind.LOCATED_IN)
        for r in rooms])
    items = [Entity(household=household, entity_type=eq_t, name=f"Item {i}",
                    model_number=f"M{i}", serial_number=f"S{i}") for i in range(9_900)]
    Entity.objects.bulk_create(items, batch_size=1000)
    EntityRelationship.objects.bulk_create([
        EntityRelationship(household=household, source=e, target=rooms[i % 100],
                           kind=RelationKind.LOCATED_IN) for i, e in enumerate(items)],
        batch_size=1000)
    today = dt.date.today()
    ActivityEntry.objects.bulk_create([
        ActivityEntry(id=uuid.uuid4(), household=household, entity=items[i % 9_900],
                      event_type="note", occurred_on=today - dt.timedelta(days=i % 3000),
                      title=f"Note {i}", notes="Routine check, nothing to report", author=owner)
        for i in range(100_000)], batch_size=5000)
    Document.objects.bulk_create([Document(household=household, display_name=f"Doc {i}")
                                  for i in range(10_000)], batch_size=2000)
    Task.objects.bulk_create([Task(household=household, entity=items[i], title=f"Task {i}",
                                   due_date=today + dt.timedelta(days=i % 60 - 10))
                              for i in range(2000)], batch_size=1000)

    client = Client()
    login(client, owner)
    targets = {
        "dashboard": ["/dashboard/"],
        "entity": [items[i * 97].get_absolute_url() for i in range(20)]
        + [rooms[0].get_absolute_url(), house.get_absolute_url()],
        "search": ["/search/?q=Item 12", "/search/?q=Routine", "/search/?q=Doc 99",
                   "/search/?q=M4321"],
        "lists": ["/items/", "/tasks/", "/documents/", "/explore/"],
    }
    results = {}
    for name, urls in targets.items():
        samples = []
        for _ in range(3):
            for url in urls:
                start = time.perf_counter()
                r = client.get(url)
                samples.append(time.perf_counter() - start)
                assert r.status_code == 200, url
        results[name] = p95(samples)
    print("p95 seconds:", results)
    assert results["dashboard"] < 1.0
    assert results["entity"] < 1.0
    assert results["lists"] < 1.0
    assert results["search"] < 2.0
