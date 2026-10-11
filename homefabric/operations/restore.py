"""Full-instance restore with staging, rollback copy and a recoverable cutover.

Cutover steps are recorded in ``DATA_DIR/restore-journal.json`` so an
interrupted restore can be completed or rolled back with
``manage.py restore --recover``. Directory renames are used because they are
supported on Windows, macOS and Linux when nothing holds the files open; the
web server and scheduler must be stopped first.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sqlite3
from pathlib import Path

from django.conf import settings
from django.db import connections

from homefabric.operations import maintenance_lock
from homefabric.operations.archive import BackupError, VerifyResult, verify_archive


class RestoreError(Exception):
    pass


def journal_path() -> Path:
    return settings.DATA_DIR / "restore-journal.json"


def _write_journal(data: dict) -> None:
    tmp = journal_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, journal_path())


def dry_run(archive) -> VerifyResult:
    result = verify_archive(archive)
    if result.ok:
        needed = int(result.uncompressed_size * 2.2) + 50 * 1024 * 1024
        free = shutil.disk_usage(settings.DATA_DIR).free
        if free < needed:
            result.fail(f"Not enough free disk space: need about {needed // 2**20} MiB "
                        f"(new copy plus rollback), have {free // 2**20} MiB.")
    return result


def _close_connections():
    for conn in connections.all():
        conn.close()


def _post_restore(db_file: Path, archive: Path) -> None:
    """Pause notifications, mark in-flight jobs for review, require verification."""
    now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("DELETE FROM django_session")
        conn.execute("UPDATE notifications_notificationdelivery SET state='review', "
                     "lease_owner='', lease_expires_at=NULL, last_error='In flight when the "
                     "backup was taken; review before resending' WHERE state='leased'")
        conn.execute("UPDATE operations_joblock SET owner='', expires_at=NULL")
        exists = conn.execute("SELECT COUNT(*) FROM operations_systemstate WHERE id=1").fetchone()[0]
        reason = ("Restored from backup. Verify the public URL and notification credentials, "
                  "review pending notifications, then resume delivery.")
        if exists:
            conn.execute("UPDATE operations_systemstate SET notifications_paused=1, "
                         "paused_reason=?, paused_at=?, restored_at=?, restored_from=?, "
                         "public_url_verified=0 WHERE id=1",
                         (reason, now, now, archive.name[:300]))
        else:
            conn.execute("INSERT INTO operations_systemstate (id, notifications_paused, "
                         "paused_reason, paused_at, restored_at, restored_from, "
                         "public_url_verified) VALUES (1, 1, ?, ?, ?, ?, 0)",
                         (reason, now, now, archive.name[:300]))
        conn.commit()
    finally:
        conn.close()


def _rollback(journal: dict) -> str:
    live = Path(journal["live"])
    rollback = Path(journal["rollback"])
    if not rollback.exists():
        return "nothing to roll back"
    if live.exists():
        failed = live.with_name(f"failed-restore-{journal['stamp']}")
        os.replace(live, failed)
    os.replace(rollback, live)
    return "previous instance data reinstated"


def recover() -> str:
    path = journal_path()
    if not path.exists():
        return "No interrupted restore found."
    journal = json.loads(path.read_text(encoding="utf-8"))
    if journal.get("state") == "complete":
        path.unlink()
        return "Last restore completed; journal removed."
    message = _rollback(journal)
    path.unlink()
    maintenance_lock.release()
    return f"Interrupted restore rolled back: {message}."


def restore(archive, *, fail_after: str | None = None, migrate: bool = True) -> dict:
    """Replace the live instance with ``archive``. ``fail_after`` is a test hook."""
    archive = Path(archive).resolve()
    if journal_path().exists():
        raise RestoreError("An interrupted restore journal exists. Run 'manage.py restore "
                           "--recover' first.")
    check = dry_run(archive)
    if not check.ok:
        raise RestoreError("Archive rejected: " + "; ".join(check.errors))
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    data_dir = Path(settings.DATA_DIR)
    live = Path(settings.INSTANCE_DIR)
    staging_root = data_dir / f"restore-staging-{stamp}"
    staged_instance = staging_root / "instance"
    rollback = data_dir / f"rollback-{stamp}"
    try:
        maintenance_lock.acquire("restore")
    except maintenance_lock.MaintenanceLockError as exc:
        raise RestoreError(str(exc)) from exc
    journal = {"state": "staging", "stamp": stamp, "archive": str(archive), "live": str(live),
               "staged": str(staged_instance), "rollback": str(rollback)}
    try:
        _write_journal(journal)
        extract = staging_root / "extract"
        result = verify_archive(archive, extract_to=extract)
        if not result.ok:
            raise RestoreError("Archive failed verification during extraction: "
                               + "; ".join(result.errors))
        staged_instance.mkdir(parents=True)
        os.replace(extract / "db.sqlite3", staged_instance / "db.sqlite3")
        (staged_instance / "blobs").mkdir()
        if (extract / "blobs").exists():
            os.replace(extract / "blobs", staged_instance / "blobs")
            (staged_instance / "blobs").mkdir(exist_ok=True)
        _post_restore(staged_instance / "db.sqlite3", archive)
        if fail_after == "staging":
            raise RestoreError("Simulated failure after staging")

        _close_connections()
        journal["state"] = "cutover"
        _write_journal(journal)
        os.replace(live, rollback)
        journal["state"] = "live-moved"
        _write_journal(journal)
        if fail_after == "live-moved":
            raise RestoreError("Simulated failure during cutover")
        os.replace(staged_instance, live)
        journal["state"] = "new-in-place"
        _write_journal(journal)
        if fail_after == "new-in-place":
            raise RestoreError("Simulated failure after cutover")
        if migrate:
            from django.core.management import call_command

            _close_connections()
            call_command("migrate", interactive=False, verbosity=0)
        journal["state"] = "complete"
        _write_journal(journal)
    except BaseException as exc:
        _close_connections()
        state = journal.get("state")
        if state in ("live-moved", "new-in-place"):
            _rollback(journal)
        shutil.rmtree(staging_root, ignore_errors=True)
        journal_path().unlink(missing_ok=True)
        maintenance_lock.release()
        if isinstance(exc, (RestoreError, BackupError)):
            raise
        raise RestoreError(f"Restore failed and the previous instance was kept: {exc}") from exc
    shutil.rmtree(staging_root, ignore_errors=True)
    journal_path().unlink(missing_ok=True)
    maintenance_lock.release()
    _close_connections()
    return {"rollback_copy": str(rollback), "manifest": check.manifest}
