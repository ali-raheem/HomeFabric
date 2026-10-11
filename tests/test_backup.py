"""Backup, restore and export: A12, A13, A14 (spec §14).

Restores run through the real CLI in a subprocess against a separate data
directory, as an operator would on a new host.
"""

import datetime as dt
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from homefabric.accounts import services as accounts
from homefabric.assets import services as assets
from homefabric.assets.models import Entity, EntityRelationship, RelationKind
from homefabric.documents import services as docs
from homefabric.documents.models import DocumentRevision
from homefabric.maintenance import services as maint
from homefabric.notifications.models import (
    DeliveryKind,
    DeliveryState,
    NotificationDelivery,
    NotificationEndpoint,
)
from homefabric.operations import maintenance_lock
from homefabric.operations.archive import BackupError, create_backup, verify_archive
from homefabric.operations.export import write_export
from tests.conftest import PDF_BYTES, login, pdf, seed_type

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.django_db(transaction=True)


def run_cli(data_dir: Path, *args, check=False):
    env = {**os.environ, "FABRIC_DATA_DIR": str(data_dir),
           "DJANGO_SETTINGS_MODULE": "homefabric.settings",
           "FABRIC_SECRET_KEY": "another-host-secret-key-0123456789abcdef",
           "FABRIC_ENABLE_LOG_CHANNEL": "1", "FABRIC_LOG_LEVEL": "ERROR"}
    proc = subprocess.run([sys.executable, str(ROOT / "manage.py"), *args], env=env,
                          capture_output=True, text=True, cwd=ROOT, timeout=300)
    if check and proc.returncode != 0:
        raise AssertionError(proc.stdout + proc.stderr)
    return proc


@pytest.fixture
def populated(home, owner, household):
    assets.add_relationship(home["radiator"], home["heating"], RelationKind.PART_OF, actor=owner)
    assets.add_relationship(home["boiler"], home["filter"], RelationKind.ASSOCIATED, actor=owner)
    manual = docs.upload_document(household, pdf("manual.pdf", PDF_BYTES + b"manual"),
                                  actor=owner, entity=home["boiler"])
    docs.add_revision(manual, pdf("manual2.pdf", PDF_BYTES + b"manual v2"), actor=owner,
                      expected_version=manual.version)
    task = maint.create_task(home["boiler"], actor=owner, title="Service",
                             due_date=dt.date.today() - dt.timedelta(days=2))
    endpoint = NotificationEndpoint.objects.create(user=owner, provider="log", display_name="Hook",
                                                   secret_value="ntfy://very-secret-topic")
    now = timezone.now()
    NotificationDelivery.objects.create(
        household=household, kind=DeliveryKind.REMINDER, event_key="k1", recipient=owner,
        endpoint=endpoint, channel="log", task=task, subject="pending", body="b", due_at=now,
        next_attempt_at=now - dt.timedelta(days=1))
    NotificationDelivery.objects.create(
        household=household, kind=DeliveryKind.REMINDER, event_key="k2", recipient=owner,
        endpoint=endpoint, channel="log", task=task, subject="in flight", body="b", due_at=now,
        next_attempt_at=now, state=DeliveryState.LEASED, lease_owner="w",
        lease_expires_at=now + dt.timedelta(minutes=5))
    accounts.create_invitation(household, role="viewer", email="", actor=owner)
    s = SessionStore()
    s["x"] = 1
    s.create()
    return {"manual": manual, "task": task}


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_a12_backup_during_writes_and_restore_on_new_host(populated, home, owner, tmp_path,
                                                          settings):
    settings.MAINTENANCE_DRAIN_SECONDS = 0.3
    from django.contrib.auth.hashers import make_password

    # The "new host" uses production hashers, not the fast test hasher.
    owner.password = make_password("Sufficiently-Long-Pass-1", hasher="pbkdf2_sha256")
    owner.save(update_fields=["password"])
    statuses = []
    stop = threading.Event()

    def writer():
        from django.db import connections

        c = Client()
        # force_login, not login(): authenticating would rehash the password with the fast
        # test hasher, which the "new host" (production hashers) cannot verify.
        c.force_login(owner)
        i = 0
        while not stop.is_set() and i < 200:
            r = c.post(reverse("assets:entity_create"),
                       {"name": f"Concurrent {i}", "entity_type": seed_type("equipment").pk,
                        "cost_currency": "GBP"})
            statuses.append(r.status_code)
            i += 1
        connections.close_all()

    t = threading.Thread(target=writer)
    t.start()
    try:
        manifest = create_backup(tmp_path / "backup.zip")
    finally:
        stop.set()
        t.join()
    assert set(statuses) <= {302, 503}
    archive = Path(manifest["archive"])
    assert verify_archive(archive).ok
    expected_entities = manifest["counts"]["assets_entity"]

    new_host = tmp_path / "newhost"
    run_cli(new_host, "migrate", "--no-input", check=True)
    dry = run_cli(new_host, "restore", str(archive), "--dry-run", "--json")
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert json.loads(dry.stdout)["ok"]
    result = run_cli(new_host, "restore", str(archive), "--confirm", "--json")
    assert result.returncode == 0, result.stdout + result.stderr

    db = sqlite3.connect(new_host / "instance" / "db.sqlite3")
    assert db.execute("SELECT COUNT(*) FROM assets_entity").fetchone()[0] == expected_entities
    restored_ids = {r[0] for r in db.execute("SELECT id FROM assets_entity")}
    original_ids = {str(e.pk).replace("-", "") for e in Entity.objects.filter(
        name__in=[e.name for e in home.values()])}
    assert original_ids <= restored_ids
    assert db.execute("SELECT COUNT(*) FROM assets_entityrelationship").fetchone()[0] == \
        manifest["counts"]["assets_entityrelationship"]
    for rev in DocumentRevision.objects.select_related("blob"):
        path = new_host / "instance" / "blobs" / rev.blob.storage_key
        assert file_sha(path) == rev.sha256
    # Sessions, tokens and provider credentials did not travel.
    assert db.execute("SELECT COUNT(*) FROM django_session").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM accounts_invitation WHERE revoked_at IS NULL "
                      "AND used_at IS NULL").fetchone()[0] == 0
    secret, needs = db.execute("SELECT secret_value, needs_verification FROM "
                               "notifications_notificationendpoint").fetchone()
    assert secret == "" and needs == 1
    assert b"very-secret-topic" not in archive.read_bytes()
    # A14: paused, in-flight jobs marked for review, nothing sent on the next tick.
    paused, public_ok = db.execute("SELECT notifications_paused, public_url_verified FROM "
                                   "operations_systemstate").fetchone()
    assert paused == 1 and public_ok == 0
    states = dict(db.execute("SELECT subject, state FROM notifications_notificationdelivery"))
    assert states == {"pending": "pending", "in flight": "review"}
    db.close()
    tick = run_cli(new_host, "process_notifications", "--once", "--json")
    assert tick.returncode == 0 and json.loads(tick.stdout)["dispatch"].get("paused")
    status = json.loads(run_cli(new_host, "notifications", "--json", "status").stdout)
    assert status["paused"] and status["pending"] == 1 and status["review"] == 1
    # Logins still work with the restored password hashes.
    check = run_cli(new_host, "shell", "-c",
                    "from django.contrib.auth import authenticate;"
                    "print(bool(authenticate(username='owner', password="
                    "'Sufficiently-Long-Pass-1')))")
    assert check.stdout.strip().endswith("True"), check.stderr


def test_a13_rejects_corrupt_malicious_and_unsupported_archives(populated, tmp_path):
    good = Path(create_backup(tmp_path / "good.zip")["archive"])
    bad = {}
    data = good.read_bytes()
    (tmp_path / "truncated.zip").write_bytes(data[: len(data) // 2])
    bad["truncated"] = tmp_path / "truncated.zip"

    def rewrite(name, mutate):
        out = tmp_path / name
        with zipfile.ZipFile(good) as src, zipfile.ZipFile(out, "w") as dst:
            entries = {i.filename: src.read(i.filename) for i in src.infolist()}
            mutate(entries, dst)
            for k, v in entries.items():
                dst.writestr(k, v)
        bad[name] = out

    def tamper_db(entries, dst):
        entries["db.sqlite3"] = entries["db.sqlite3"][:-10] + b"0123456789"

    def traversal(entries, dst):
        entries["../../evil.txt"] = b"x"

    def symlink(entries, dst):
        info = zipfile.ZipInfo("blobs/" + "a" * 32 + "/aa/" + "b" * 32)
        info.external_attr = (0o120777 << 16)
        dst.writestr(info, "/etc/passwd")

    def newer_format(entries, dst):
        m = json.loads(entries["manifest.json"])
        m["format_version"] = 99
        entries["manifest.json"] = json.dumps(m).encode()
        entries["manifest.sha256"] = hashlib.sha256(entries["manifest.json"]).hexdigest().encode()

    def newer_schema(entries, dst):
        m = json.loads(entries["manifest.json"])
        m["schema"]["assets"].append("0099_from_the_future")
        entries["manifest.json"] = json.dumps(m).encode()
        entries["manifest.sha256"] = hashlib.sha256(entries["manifest.json"]).hexdigest().encode()

    def bomb(entries, dst):
        entries["blobs/" + "c" * 32 + "/cc/" + "d" * 32] = b"\0" * (30 * 1024 * 1024)

    for name, fn in [("tamper.zip", tamper_db), ("traversal.zip", traversal),
                     ("symlink.zip", symlink), ("newer_format.zip", newer_format),
                     ("newer_schema.zip", newer_schema), ("bomb.zip", bomb)]:
        rewrite(name, fn)
    # Compress the bomb so its ratio is extreme.
    with zipfile.ZipFile(bad["bomb.zip"]) as z:
        items = {i.filename: z.read(i.filename) for i in z.infolist()}
    with zipfile.ZipFile(bad["bomb.zip"], "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in items.items():
            z.writestr(k, v)

    for name, path in bad.items():
        result = verify_archive(path)
        assert not result.ok, name

    host = tmp_path / "host"
    run_cli(host, "migrate", "--no-input", check=True)
    before = (host / "instance" / "db.sqlite3").read_bytes()
    for name, path in bad.items():
        proc = run_cli(host, "restore", str(path), "--confirm")
        assert proc.returncode != 0, name
    assert (host / "instance" / "db.sqlite3").read_bytes() == before
    assert not list(host.glob("rollback-*"))
    assert not (host / "maintenance.lock").exists()


def test_a13_simulated_cutover_failure_keeps_previous_instance(populated, tmp_path):
    archive = Path(create_backup(tmp_path / "b.zip")["archive"])
    host = tmp_path / "host"
    run_cli(host, "migrate", "--no-input", check=True)
    marker = run_cli(host, "create_household", "Keep me", "--owner", "nobody")
    assert marker.returncode != 0  # sanity: CLI reports failures with nonzero exit
    before = (host / "instance" / "db.sqlite3").read_bytes()
    for stage in ("staging", "live-moved", "new-in-place"):
        script = ("import django,os;os.environ.setdefault('DJANGO_SETTINGS_MODULE',"
                  "'homefabric.settings');django.setup();"
                  "from homefabric.operations.restore import restore, RestoreError\n"
                  f"try:\n    restore(r'{archive}', fail_after='{stage}')\n"
                  "except RestoreError as e:\n    print('FAILED', e)")
        proc = run_cli(host, "shell", "-c", script)
        assert "FAILED" in proc.stdout, proc.stdout + proc.stderr
        assert (host / "instance" / "db.sqlite3").read_bytes() == before, stage
        assert not (host / "restore-journal.json").exists()
        assert not (host / "maintenance.lock").exists()


def test_interrupted_restore_recovered_from_journal(tmp_path):
    host = tmp_path / "host"
    run_cli(host, "migrate", "--no-input", check=True)
    live = host / "instance"
    original = (live / "db.sqlite3").read_bytes()
    rollback = host / "rollback-x"
    os.replace(live, rollback)  # simulate a crash right after moving live data aside
    (host / "restore-journal.json").write_text(json.dumps({
        "state": "live-moved", "stamp": "x", "live": str(live), "rollback": str(rollback),
        "staged": str(host / "nowhere")}))
    proc = run_cli(host, "restore", "--recover")
    assert proc.returncode == 0, proc.stderr
    assert (live / "db.sqlite3").read_bytes() == original


def test_failed_backup_does_not_overwrite_last_archive(populated, tmp_path):
    target = tmp_path / "nightly.zip"
    create_backup(target)
    original = target.read_bytes()
    blob = DocumentRevision.objects.first().blob
    from homefabric.documents import storage

    storage.path_for(blob.storage_key).unlink()
    with pytest.raises(BackupError):
        create_backup(target)
    assert target.read_bytes() == original
    assert not maintenance_lock.is_locked()
    assert not list(tmp_path.glob(".nightly.zip.partial*"))


def test_backup_lock_blocks_web_writes(owner, household, client):
    login(client, owner)
    maintenance_lock.acquire("backup")
    try:
        r = client.post(reverse("assets:entity_create"), {"name": "x"})
        assert r.status_code == 503 and r["Retry-After"]
        assert client.get(reverse("core:dashboard")).status_code == 200
    finally:
        maintenance_lock.release()


def test_scheduler_skips_during_maintenance(db, tmp_path):
    from homefabric.operations.jobs import JobSkipped, run_job

    maintenance_lock.acquire("backup")
    try:
        with pytest.raises(JobSkipped):
            run_job("process_schedules", lambda: {})
    finally:
        maintenance_lock.release()


def test_household_export_contents(populated, home, household, owner, other_household,
                                   other_owner, tmp_path, make_entity):
    make_entity(other_household, "Not mine", actor=other_owner)
    out = write_export(household, tmp_path / "export.zip")
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        data = json.loads(z.read("export.json"))
        assert {"export.json", "entities.csv", "tasks.csv", "activity.csv",
                "documents.csv", "README.txt"} <= names
        assert data["format"] == "homefabric-household-export" and data["format_version"] == 1
        ids = {e["id"] for e in data["entities"]}
        assert str(home["boiler"].pk) in ids and "Not mine" not in z.read("entities.csv").decode()
        rel_kinds = {r["kind"] for r in data["relationships"]}
        assert {"located_in", "part_of", "associated"} <= rel_kinds
        revs = [r for d in data["documents"] for r in d["revisions"]]
        assert len(revs) == 2
        for r in revs:
            assert hashlib.sha256(z.read(r["path"])).hexdigest() == r["sha256"]
        blob = z.read("export.json")
        assert b"very-secret-topic" not in blob and b"password" not in blob.lower()
        assert EntityRelationship.objects.filter(household=household).count() == \
            len(data["relationships"])
