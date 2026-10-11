"""Printable house manual: structure, cross-references, outputs and access."""

import datetime as dt
import hashlib
import io
import json
import re
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse

from homefabric.assets import services as assets
from homefabric.assets.models import RelationKind
from homefabric.documents import services as docs
from homefabric.maintenance import services as maint
from homefabric.manual.builder import ManualBuilder
from homefabric.manual.render import write_html, write_zip
from tests.conftest import PNG_BYTES, login, pdf
from tests.test_web import check_page

pytestmark = pytest.mark.django_db


@pytest.fixture
def house(home, owner, household, make_entity):
    """Boiler in the kitchen, part of two systems; plus odds and ends."""
    kitchen = make_entity(household, "Kitchen", "room", location=home["house"])
    assets.set_location(home["boiler"], kitchen, actor=owner)
    hot_water = make_entity(household, "Hot water", "system")
    for system in (home["heating"], hot_water):
        assets.add_relationship(home["boiler"], system, RelationKind.PART_OF, actor=owner)
    assets.add_relationship(home["radiator"], home["heating"], RelationKind.PART_OF, actor=owner)
    pump = make_entity(household, "Circulation pump", "component")  # no location
    assets.add_relationship(pump, home["heating"], RelationKind.PART_OF, actor=owner)
    cupboard = make_entity(household, "Airing cupboard", "equipment", location=home["bedroom"])
    tank = make_entity(household, "Cylinder", "equipment", location=cupboard)
    loose = make_entity(household, "Spare keys", "equipment")
    policy = make_entity(household, "Home insurance", "policy", provider="Acme")
    assets.add_relationship(policy, home["house"], RelationKind.COVERS, actor=owner)
    return {**home, "kitchen": kitchen, "hot_water": hot_water, "pump": pump,
            "cupboard": cupboard, "tank": tank, "loose": loose, "policy": policy}


def build(household, **kw):
    kw.setdefault("link_mode", "app")
    return ManualBuilder(household, **kw).build()


def numbers(manual):
    return {p.entity.name: p.number for p in manual.pages if p.entity is not None}


def test_every_item_has_exactly_one_page(house, household):
    m = build(household)
    names = [p.entity.name for p in m.pages if p.entity]
    assert sorted(names) == sorted(e.name for e in house.values())
    assert len(names) == len(set(names))


def test_contents_organised_by_room_then_system_then_rest(house, household):
    m = build(household)
    n = numbers(m)
    parts = [p["key"] for p in m.parts]
    assert parts == ["locations", "systems", "policies", "other"]
    # Boiler is printed inside the kitchen chapter, not in a system chapter.
    assert n["Boiler"].startswith(n["Kitchen"] + ".")
    assert n["Kitchen"].startswith(n["House"] + ".")
    # Items located inside items nest beneath them.
    assert n["Cylinder"].startswith(n["Airing cupboard"] + ".")
    # Unlocated system members get their page in the system chapter.
    assert n["Circulation pump"].startswith(n["Central heating"] + ".")
    by_part = {p.entity.name: p.part for p in m.pages if p.entity}
    assert by_part["Central heating"] == by_part["Hot water"] == "systems"
    assert by_part["Home insurance"] == "policies"
    assert by_part["Spare keys"] == "other"
    entries = [c.title for c in m.contents]
    assert entries.index("House") < entries.index("Central heating") < entries.index(
        "Home insurance") < entries.index("Spare keys")


def test_item_page_cross_references_location_and_both_systems(house, household, owner):
    maint.create_task(house["boiler"], actor=owner, title="Fix pressure",
                      due_date=dt.date.today() + dt.timedelta(days=5))
    maint.create_schedule(house["boiler"], actor=owner, title="Annual service", mode="fixed",
                          interval_count=1, interval_unit="year",
                          anchor_date=dt.date.today() + dt.timedelta(days=30))
    maint.add_activity(house["boiler"], actor=owner, event_type="service",
                       occurred_on=dt.date.today(), title="Serviced", provider="Gas Safe",
                       amount="80", currency="GBP")
    m = build(household)
    n = numbers(m)
    page = next(p for p in m.pages if p.entity == house["boiler"])
    assert [r.name for r in page.path] == ["House", "Kitchen"]
    assert {(r.name, r.number) for r in page.part_of} == {
        ("Central heating", n["Central heating"]), ("Hot water", n["Hot water"])}
    assert {t["title"] for t in page.tasks} == {"Annual service", "Fix pressure"}
    assert page.schedules[0].title == "Annual service" and page.schedules[0].next_due
    assert page.history[0]["cost"] == "£80.00"
    system = next(p for p in m.pages if p.entity == house["heating"])
    members = {r.name: r.number for r in system.components}
    assert members["Boiler"] == n["Boiler"] and members["Bedroom radiator"] == n["Bedroom radiator"]
    policy = next(p for p in m.pages if p.entity == house["policy"])
    assert [r.name for r in policy.covers] == ["House"]
    assert {r["title"] for r in m.upcoming} >= {"Fix pressure"}


def test_history_options(house, household, owner):
    for i in range(8):
        maint.add_activity(house["boiler"], actor=owner, event_type="note",
                           occurred_on=dt.date(2025, 1, 1) + dt.timedelta(days=i), title=f"n{i}")
    page = lambda m: next(p for p in m.pages if p.entity == house["boiler"])  # noqa: E731
    recent = page(build(household))
    assert [h["title"] for h in recent.history] == ["n7", "n6", "n5", "n4", "n3"]
    assert recent.history_truncated
    assert len(page(build(household, history="all")).history) == 8
    assert page(build(household, history="none")).history == []


def test_archived_excluded_by_default(house, household, owner):
    radiator = house["radiator"]
    assets.archive_entity(radiator, actor=owner, expected_version=radiator.version,
                          outstanding=None)
    assert "Bedroom radiator" not in numbers(build(household))
    assert "Bedroom radiator" in numbers(build(household, include_archived=True))


def test_other_household_never_included(house, household, other_household, other_owner,
                                        make_entity):
    make_entity(other_household, "Their boiler", actor=other_owner)
    html = write_html(household, _tmp("x.html")).read_text()
    assert "Their boiler" not in html


def _tmp(name):
    import tempfile
    from pathlib import Path

    return Path(tempfile.mkdtemp()) / name


def test_chart_colour_coded_with_text_kinds(house, household):
    m = build(household)
    assert [c.title for c in m.charts] == ["Where things are",
                                          "How things work together (systems)"]
    rows = [r for b in m.charts[0].blocks for r in b.rows]
    boiler = next(r for r in rows if r["label"] == "Boiler")
    room = next(r for r in rows if r["label"] == "Kitchen")
    assert boiler["fill"] != room["fill"] and boiler["kind"] == "Equipment"
    system_rows = [r["label"] for b in m.charts[1].blocks for r in b.rows]
    assert system_rows.count("Boiler") == 2  # appears under both systems


def test_large_chart_splits_into_printable_blocks(household, make_entity):
    from homefabric.manual.builder import CHART_ROWS_PER_BLOCK

    house = make_entity(household, "House", "property")
    for i in range(CHART_ROWS_PER_BLOCK + 5):
        make_entity(household, f"Room {i:03}", "room", location=house)
    m = build(household)
    assert len(m.charts[0].blocks) == 2


def test_view_renders_for_viewer_and_meets_a11y(house, household, viewer, client):
    login(client, viewer)
    r = client.get(reverse("manual:view"))
    html = r.content.decode()
    assert r.status_code == 200 and "<svg class=\"chart\"" in html
    check_page(html, "manual")
    assert "manual.css" in html and "<style>" not in html  # CSP-friendly in-app view
    assert client.get(reverse("manual:options")).status_code == 200


def test_html_download_is_self_contained(house, household, owner, client):
    docs.upload_document(household, SimpleUploadedFile("front.png", PNG_BYTES), actor=owner,
                         entity=house["boiler"], category="photo", display_name="Front")
    login(client, owner)
    r = client.get(reverse("manual:download"), {"format": "html", "history": "recent",
                                                "photos": "on"})
    assert r["Content-Disposition"].startswith("attachment") and ".html" in r[
        "Content-Disposition"]
    html = r.content.decode()
    assert "<style>" in html and "data:image/png;base64," in html
    for src in re.findall(r'(?:src|href)="([^"#][^"]*)"', html):
        assert src.startswith("data:"), src  # no links back into the app


def test_zip_bundles_documents_with_relative_links(house, household, owner, client):
    manual_doc = docs.upload_document(household, pdf("boiler-manual.pdf"), actor=owner,
                                      entity=house["boiler"], category="manual")
    login(client, owner)
    r = client.get(reverse("manual:download"), {"format": "zip"})
    z = zipfile.ZipFile(io.BytesIO(b"".join(r.streaming_content)))
    index = next(n for n in z.namelist() if n.endswith("index.html"))
    root = index.rsplit("/", 1)[0]
    html = z.read(index).decode()
    links = re.findall(r'href="(documents/[^"]+)"', html)
    assert links
    data = z.read(f"{root}/{links[0]}")
    assert hashlib.sha256(data).hexdigest() == manual_doc.current_revision.sha256


def test_zip_requires_export_permission(house, household, viewer, client):
    login(client, viewer)
    assert client.get(reverse("manual:download"), {"format": "zip"}).status_code == 403
    assert client.get(reverse("manual:download"), {"format": "html"}).status_code == 200


def test_other_household_cannot_view(house, other_owner, other_household, client):
    login(client, other_owner)
    html = client.get(reverse("manual:view")).content.decode()
    assert "Boiler" not in html


def test_cli(house, household, tmp_path):
    out = io.StringIO()
    call_command("house_manual", household.name, "--output", str(tmp_path / "m.zip"), "--json",
                 stdout=out)
    assert json.loads(out.getvalue())["ok"]
    assert zipfile.ZipFile(tmp_path / "m.zip").namelist()
    call_command("house_manual", str(household.pk), "--output", str(tmp_path / "m.html"),
                 "--history", "none", stdout=io.StringIO())
    assert "House manual" in (tmp_path / "m.html").read_text()


def test_write_zip_function(house, household, tmp_path):
    path = write_zip(household, tmp_path / "x.zip")
    assert any(n.endswith("index.html") for n in zipfile.ZipFile(path).namelist())


def test_manual_respects_sort_order(household, make_entity):
    house = make_entity(household, "House", "property")
    make_entity(household, "First floor", "floor", location=house, sort_order=2)
    make_entity(household, "Ground floor", "floor", location=house, sort_order=1)
    n = numbers(build(household))
    assert n["Ground floor"] == "1.1" and n["First floor"] == "1.2"
    rows = [r["label"] for b in build(household).charts[0].blocks for r in b.rows]
    assert rows.index("Ground floor") < rows.index("First floor")
