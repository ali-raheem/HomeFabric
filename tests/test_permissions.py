"""Authorization and accounts: A09, A15, invitations, throttling, last-owner rule (spec §9)."""

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from homefabric.accounts import services
from homefabric.accounts.models import Invitation, Membership, Role
from homefabric.core.errors import DomainError
from homefabric.documents import services as docs
from homefabric.maintenance import services as maint
from tests.conftest import PASSWORD, login, make_user, pdf

pytestmark = pytest.mark.django_db


def test_a09_viewer_cannot_mutate(home, viewer, client, today):
    task = maint.create_task(home["boiler"], actor=home["boiler"].created_by, title="T")
    login(client, viewer)
    assert client.get(home["boiler"].get_absolute_url()).status_code == 200
    posts = [
        (reverse("assets:entity_create"), {"name": "x", "entity_type": 5}),
        (reverse("assets:entity_edit", args=[home["boiler"].pk]), {"name": "x"}),
        (reverse("assets:entity_archive", args=[home["boiler"].pk]), {"version": 1}),
        (reverse("assets:relationship_add", args=[home["boiler"].pk]), {}),
        (reverse("maintenance:task_create"), {}),
        (reverse("maintenance:task_complete", args=[task.pk]), {}),
        (reverse("maintenance:task_skip", args=[task.pk]), {"reason": "x"}),
        (reverse("maintenance:activity_create", args=[home["boiler"].pk]), {}),
        (reverse("documents:upload"), {}),
        (reverse("accounts:members"), {"role": "owner"}),
        (reverse("accounts:household_settings"), {}),
        (reverse("operations:household_export"), {}),
    ]
    for url, data in posts:
        assert client.post(url, data).status_code == 403, url
    task.refresh_from_db()
    assert task.status == "open"


def test_a09_viewer_can_download_documents(home, owner, viewer, client):
    doc = docs.upload_document(home["boiler"].household, pdf(), actor=owner)
    login(client, viewer)
    r = client.get(reverse("documents:download", args=[doc.current_revision.pk]))
    assert r.status_code == 200


def test_a09_other_household_cannot_reach_anything(home, owner, other_owner, other_household,
                                                   client):
    doc = docs.upload_document(home["boiler"].household, pdf(), actor=owner)
    task = maint.create_task(home["boiler"], actor=owner, title="Secret task")
    login(client, other_owner)
    urls = [home["boiler"].get_absolute_url(),
            reverse("documents:download", args=[doc.current_revision.pk]),
            reverse("documents:detail", args=[doc.pk]),
            task.get_absolute_url(),
            reverse("assets:explorer_children", args=[home["house"].pk])]
    for url in urls:
        assert client.get(url).status_code == 404, url
    r = client.get(reverse("core:search"), {"q": "Boiler"})
    assert b"Boiler" not in r.content.split(b'id="r-items"')[-1]
    r = client.get(reverse("core:search"), {"q": "Secret"})
    assert b"Secret task" not in r.content
    # Switching to a household they are not a member of is ignored.
    client.post(reverse("accounts:switch_household"), {"household": home["house"].household_id})
    assert client.get(home["boiler"].get_absolute_url()).status_code == 404
    # Export only contains their household.
    r = client.post(reverse("operations:household_export"))
    import io
    import zipfile

    data = zipfile.ZipFile(io.BytesIO(b"".join(r.streaming_content))).read("export.json")
    assert b"Boiler" not in data


def test_unauthenticated_access_redirects(home, client):
    for url in (home["boiler"].get_absolute_url(), reverse("core:dashboard"),
                reverse("documents:list")):
        r = client.get(url)
        assert r.status_code == 302 and "/login/" in r["Location"]


def test_a15_deactivated_member_loses_session_and_access(home, owner, editor, client,
                                                         household):
    login(client, editor)
    assert client.get(home["boiler"].get_absolute_url()).status_code == 200
    entry = maint.add_activity(home["boiler"], actor=editor, event_type="note",
                               occurred_on=dt.date.today(), title="Editor note")
    services.deactivate_membership(Membership.objects.get(user=editor, household=household),
                                   actor=owner)
    r = client.get(home["boiler"].get_absolute_url())
    assert r.status_code == 302 and "/login/" in r["Location"]  # session invalidated
    login(client, editor)
    assert client.get(home["boiler"].get_absolute_url()).status_code == 302  # no household
    entry.refresh_from_db()
    assert entry.author == editor  # authored history preserved


def test_user_deactivation_instance_wide(admin_user, editor, owner, client):
    login(client, editor)
    services.deactivate_user(editor, actor=admin_user)
    assert client.get(reverse("core:dashboard")).status_code == 302
    assert not client.login(username="editor", password=PASSWORD)


def test_last_owner_cannot_be_removed_or_demoted(household, owner, editor):
    m = Membership.objects.get(user=owner, household=household)
    with pytest.raises(DomainError, match="last active household owner"):
        services.deactivate_membership(m, actor=owner)
    with pytest.raises(DomainError):
        services.change_role(m, Role.EDITOR, actor=owner)
    services.change_role(Membership.objects.get(user=editor), Role.OWNER, actor=owner)
    services.change_role(m, Role.EDITOR, actor=owner)
    with pytest.raises(DomainError):
        services.deactivate_user(editor, actor=owner)


def test_household_owner_is_not_instance_admin(owner, client, household):
    login(client, owner)
    assert client.get(reverse("operations:instance_status")).status_code == 403
    assert client.get(reverse("accounts:user_list")).status_code == 403


def test_instance_admin_reads_across_instance(home, admin_user, client):
    login(client, admin_user)
    client.post(reverse("accounts:switch_household"), {"household": home["house"].household_id})
    assert client.get(home["boiler"].get_absolute_url()).status_code == 200


def test_invitation_single_use_and_expiry(household, owner, client):
    inv, url = services.create_invitation(household, role=Role.EDITOR, email="", actor=owner)
    token = url.rstrip("/").rsplit("/", 1)[-1]
    r = client.post(reverse("accounts:accept_invitation", args=[token]), {
        "username": "newbie", "password1": "Another-Long-Pass-9", "password2":
        "Another-Long-Pass-9"})
    assert r.status_code == 302
    assert Membership.objects.get(user__username="newbie").role == Role.EDITOR
    client.logout()
    r = client.get(reverse("accounts:accept_invitation", args=[token]))
    assert r.status_code == 410
    inv2, url2 = services.create_invitation(household, role=Role.VIEWER, email="", actor=owner)
    Invitation.objects.filter(pk=inv2.pk).update(expires_at=timezone.now() - dt.timedelta(1))
    assert client.get(reverse("accounts:accept_invitation",
                              args=[url2.rsplit("/", 2)[-2]])).status_code == 410
    assert not Invitation.objects.filter(token_hash=token).exists()  # stored hashed


def test_invitation_email_sent_when_configured(household, owner, client):
    from django.core import mail

    login(client, owner)
    client.post(reverse("accounts:members"), {"role": "viewer", "email": "friend@example.test"})
    assert len(mail.outbox) == 1 and "invite" in mail.outbox[0].body


def test_login_throttling(owner, client):
    for _ in range(5):
        client.post(reverse("accounts:login"), {"username": "owner", "password": "wrong"})
    r = client.post(reverse("accounts:login"), {"username": "owner", "password": PASSWORD})
    assert r.status_code == 200 and b"Too many failed" in r.content


def test_no_public_registration(client, db):
    assert client.get("/register/").status_code == 404
    assert client.get("/signup/").status_code == 404


def test_password_setup_link_for_recovery(owner, client):
    url = services.password_setup_url(owner)
    path = url.replace("https://home.example.test", "")
    r = client.get(path, follow=True)
    assert r.status_code == 200 and b"new password" in r.content.lower()


def test_sign_out_everywhere(owner, household, client):
    from django.test import Client

    other = Client()
    login(other, owner)
    login(client, owner)
    client.post(reverse("accounts:sign_out_everywhere"))
    assert client.get(reverse("core:dashboard")).status_code == 200
    assert other.get(reverse("core:dashboard")).status_code == 302


def test_csrf_enforced(owner, household):
    from django.test import Client

    c = Client(enforce_csrf_checks=True)
    login(c, owner)
    assert c.post(reverse("accounts:household_settings"), {}).status_code == 403


def test_make_user_helper(db):
    assert make_user("x").check_password(PASSWORD)
