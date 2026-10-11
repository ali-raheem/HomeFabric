"""Management command contracts: exit codes, JSON output, no secrets in argv (spec §13)."""

import io
import json

import pytest
from django.core.management import call_command

from homefabric.accounts.models import User

pytestmark = pytest.mark.django_db


def run(*args, stdin=None, monkeypatch=None):
    out = io.StringIO()
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    call_command(*args, stdout=out)
    return out.getvalue()


def test_bootstrap_admin_reads_password_from_stdin(monkeypatch, db):
    out = run("bootstrap_admin", "--username", "boss", "--password-stdin", "--household", "Home",
              "--json", stdin="A-Very-Good-Passphrase\n", monkeypatch=monkeypatch)
    data = json.loads(out)
    assert data["ok"] and data["household"]
    user = User.objects.get(username="boss")
    assert user.is_superuser and user.check_password("A-Very-Good-Passphrase")
    with pytest.raises(SystemExit):
        run("bootstrap_admin", "--username", "boss2", "--password-stdin", "--json",
            stdin="A-Very-Good-Passphrase\n", monkeypatch=monkeypatch)


def test_bootstrap_rejects_weak_password(monkeypatch, db):
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        run("bootstrap_admin", "--username", "boss", "--password-stdin", stdin="123\n",
            monkeypatch=monkeypatch)


def test_no_command_takes_password_argument():
    from django.core.management import get_commands, load_command_class

    for name, app in get_commands().items():
        if not app.startswith("homefabric"):
            continue
        parser = load_command_class(app, name).create_parser("manage.py", name)
        flags = {o for a in parser._actions for o in a.option_strings}
        assert not any("password" in f and "stdin" not in f for f in flags), name


def test_scheduler_commands_json(db):
    data = json.loads(run("process_schedules", "--once", "--json"))
    assert data["ok"]
    data = json.loads(run("process_notifications", "--once", "--json"))
    assert data["ok"] and "planned" in data


def test_check_integrity_exit_code(household, owner):
    from homefabric.documents import services, storage
    from tests.conftest import pdf

    out = json.loads(run("check_integrity", "--json"))
    assert out["ok"]
    doc = services.upload_document(household, pdf(), actor=owner)
    storage.path_for(doc.current_revision.blob.storage_key).unlink()
    with pytest.raises(SystemExit) as exc:
        run("check_integrity", "--json")
    assert exc.value.code == 1


def test_verify_backup_nonzero_on_missing(tmp_path, db):
    with pytest.raises(SystemExit):
        run("verify_backup", str(tmp_path / "missing.zip"), "--json")


def test_issue_setup_link(owner):
    data = json.loads(run("issue_setup_link", "owner", "--json"))
    assert data["url"].startswith("https://home.example.test/password-reset/")


def test_maintenance_command(db):
    from homefabric.operations import maintenance_lock

    maintenance_lock.acquire("backup")
    assert json.loads(run("maintenance", "--json"))["locked"]
    run("maintenance", "--clear")
    assert not maintenance_lock.is_locked()


def test_notifications_command(db):
    run("notifications", "pause", "--reason", "testing")
    assert json.loads(run("notifications", "--json", "status"))["paused"]
    run("notifications", "resume", "--discard-backlog", "--public-url-verified")
    assert not json.loads(run("notifications", "--json", "status"))["paused"]


def test_seed_demo_and_export(owner, tmp_path):
    run("seed_demo", "--owner", "owner", "--name", "Demo")
    from homefabric.accounts.models import Household

    hh = Household.objects.get(name="Demo")
    out = tmp_path / "demo.zip"
    run("export_household", str(hh.pk), "--output", str(out))
    assert out.stat().st_size > 0


def test_healthcheck_command(db):
    out = io.StringIO()
    with pytest.raises(SystemExit) as exc:  # scheduler has never run: not healthy
        call_command("healthcheck", "--json", stdout=out)
    assert exc.value.code == 1
    data = json.loads(out.getvalue())
    assert data["checks"]["database"] == "ok" and data["checks"]["scheduler"] == "stale"


def test_upgrade_up_to_date(db):
    assert json.loads(run("upgrade", "--json"))["applied"] == 0
