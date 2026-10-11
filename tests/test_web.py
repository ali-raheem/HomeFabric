"""Web experience: every page renders for each role, accessibility basics (A16)."""

import re
from html.parser import HTMLParser

import pytest
from django.test import Client

from homefabric.accounts.models import Membership, Role
from homefabric.operations.demo import build
from tests.conftest import login

pytestmark = pytest.mark.django_db


class A11yParser(HTMLParser):
    """Collects form controls, labels and landmarks for simple WCAG checks."""

    def __init__(self):
        super().__init__()
        self.controls, self.label_for, self.ids = [], set(), set()
        self.labelled_by_wrap = 0
        self.label_depth = 0
        self.h1 = 0
        self.html_lang = None
        self.has_main = False
        self.images_without_alt = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        if tag == "html":
            self.html_lang = a.get("lang")
        if tag == "main":
            self.has_main = True
        if tag == "h1":
            self.h1 += 1
        if tag == "img" and "alt" not in a:
            self.images_without_alt += 1
        if tag == "label":
            self.label_depth += 1
            if a.get("for"):
                self.label_for.add(a["for"])
        if tag in ("input", "select", "textarea"):
            if a.get("type") in ("hidden", "submit", "button"):
                return
            self.controls.append((a, self.label_depth > 0))

    def handle_endtag(self, tag):
        if tag == "label":
            self.label_depth -= 1


def check_page(html: str, url: str):
    p = A11yParser()
    p.feed(html)
    assert p.html_lang == "en-GB", url
    assert p.has_main, url
    assert p.h1 >= 1, url
    assert p.images_without_alt == 0, url
    for attrs, wrapped in p.controls:
        labelled = wrapped or attrs.get("id") in p.label_for or attrs.get("aria-label") \
            or attrs.get("aria-labelledby")
        assert labelled, f"{url}: unlabelled control {attrs}"
    assert 'name="viewport"' in html, url


def crawl(client, start="/dashboard/", limit=250):
    seen, queue, pages = set(), [start], {}
    while queue and len(seen) < limit:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        r = client.get(url)
        assert r.status_code in (200, 302), (url, r.status_code)
        if r.status_code == 200 and r["Content-Type"].startswith("text/html"):
            html = r.content.decode()
            pages[url] = html
            for href in re.findall(r'href="(/[^"#]*)"', html):
                href = href.replace("&amp;", "&")
                if any(x in href for x in ("logout", "django-admin", "/static/", "download")):
                    continue
                queue.append(href)
    return pages


@pytest.fixture
def demo(household, owner):
    return build(household, owner)


@pytest.mark.parametrize("role", ["owner", "editor", "viewer", "admin"])
def test_all_pages_render_and_meet_basic_accessibility(demo, household, owner, role, admin_user):
    from tests.conftest import make_user

    if role == "owner":
        user = owner
    elif role == "admin":
        user = admin_user
    else:
        user = make_user(role)
        Membership.objects.create(user=user, household=household, role=role)
    client = Client()
    login(client, user)
    pages = crawl(client)
    assert len(pages) > 30
    for url, html in pages.items():
        if "/explore/" in url and "/children/" in url:
            continue  # HTMX fragments
        if url.startswith("/schedules/preview/"):
            continue
        check_page(html, url)


def test_viewer_sees_no_edit_controls(demo, household):
    from tests.conftest import make_user

    viewer = make_user("v2")
    Membership.objects.create(user=viewer, household=household, role=Role.VIEWER)
    client = Client()
    login(client, viewer)
    html = client.get(demo["boiler"].get_absolute_url()).content.decode()
    assert "Add note or work" not in html and "/edit/" not in html


def test_security_headers(owner_client):
    r = owner_client.get("/dashboard/")
    assert "default-src 'self'" in r["Content-Security-Policy"]
    assert r["X-Frame-Options"] == "DENY"
    assert r["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in r["Cache-Control"]
    assert r["X-Request-ID"]


def test_markdown_is_sanitised(home, owner, owner_client):
    import datetime as dt

    from homefabric.maintenance import services

    services.add_activity(home["boiler"], actor=owner, event_type="note",
                          occurred_on=dt.date.today(), title="x",
                          notes="<script>alert(1)</script> **bold** [x](javascript:alert(1))")
    html = owner_client.get(home["boiler"].get_absolute_url()).content.decode()
    assert "<script>alert(1)</script>" not in html
    assert "<strong>bold</strong>" in html
    assert 'href="javascript:' not in html


def test_no_external_assets(owner_client):
    html = owner_client.get("/dashboard/").content.decode()
    for src in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert not src.startswith(("http://", "https://", "//")), src


def test_schedule_preview_fragment(owner_client):
    r = owner_client.get("/schedules/preview/", {"anchor_date": "2025-01-31", "interval_count": 1,
                                                 "interval_unit": "month", "mode": "fixed"})
    assert b"28 Feb 2025" in r.content


def test_health_endpoints(client, db):
    assert client.get("/healthz").json()["status"] == "ok"
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"


def test_search_finds_notes_models_and_documents(demo, owner_client):
    for q, expected in (("Greenstar", b"Boiler"), ("bleeding", b"Cold at the top"),
                        ("HI-998877", b"Home insurance")):
        r = owner_client.get("/search/", {"q": q})
        assert expected in r.content, q
