import datetime as dt
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from homefabric.accounts import services as accounts
from homefabric.accounts.models import Membership, Role, User
from homefabric.assets import services as assets
from homefabric.assets.models import EntityType

PASSWORD = "Sufficiently-Long-Pass-1"


def make_user(username, **extra):
    return User.objects.create_user(username=username, password=PASSWORD,
                                    email=f"{username}@example.test", **extra)


@pytest.fixture
def owner(db):
    return make_user("owner", display_name="Olive Owner")


@pytest.fixture
def household(owner):
    return accounts.create_household(name="Test home", owner=owner, timezone_name="Europe/London",
                                     currency="GBP")


@pytest.fixture
def editor(household):
    user = make_user("editor", display_name="Eddie Editor")
    Membership.objects.create(user=user, household=household, role=Role.EDITOR)
    return user


@pytest.fixture
def viewer(household):
    user = make_user("viewer")
    Membership.objects.create(user=user, household=household, role=Role.VIEWER)
    return user


@pytest.fixture
def other_owner(db):
    return make_user("stranger")


@pytest.fixture
def other_household(other_owner):
    return accounts.create_household(name="Other home", owner=other_owner)


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(username="root", password=PASSWORD,
                                         email="root@example.test")


def seed_type(kind):
    from homefabric.assets.seed import ensure_seed_types

    ensure_seed_types()
    return EntityType.objects.get(household__isnull=True, base_kind=kind)


@pytest.fixture
def make_entity(owner):
    def _make(household, name, kind="equipment", location=None, actor=None, **data):
        return assets.create_entity(household, actor=actor or owner, location=location,
                                    data={"name": name, "entity_type": seed_type(kind), **data})
    return _make


@pytest.fixture
def home(household, make_entity):
    """The boiler scenario from the specification (A01)."""
    house = make_entity(household, "House", "property")
    utility = make_entity(household, "Utility room", "room", location=house)
    bedroom = make_entity(household, "Bedroom", "room", location=house)
    heating = make_entity(household, "Central heating", "system")
    boiler = make_entity(household, "Boiler", "equipment", location=utility)
    filt = make_entity(household, "MagnaClean Professional 2", "component", location=utility)
    radiator = make_entity(household, "Bedroom radiator", "equipment", location=bedroom)
    return {"house": house, "utility": utility, "bedroom": bedroom, "heating": heating,
            "boiler": boiler, "filter": filt, "radiator": radiator}


def login(client, user):
    assert client.login(username=user.username, password=PASSWORD)
    return client


@pytest.fixture
def owner_client(client, owner, household):
    return login(client, owner)


PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG_BYTES = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
             b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
             b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def pdf(name="manual.pdf", content=PDF_BYTES + b"% padding\n"):
    return SimpleUploadedFile(name, content, content_type="application/pdf")


def docx(name="notes.docx", macro=False):
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
        if macro:
            zf.writestr("word/vbaProject.bin", b"\x00macro")
    return SimpleUploadedFile(name, buf.getvalue())


@pytest.fixture
def today():
    return dt.date.today()
