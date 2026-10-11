"""Instance-wide write freeze used by backup and restore.

The lock is a file in DATA_DIR so it is visible to every process (web server,
scheduler invocations, CLI) on every OS without extra services.
"""

import contextlib
import datetime as dt
import json
import os
import time

from django.conf import settings


class MaintenanceLockError(Exception):
    pass


def lock_path():
    return settings.DATA_DIR / "maintenance.lock"


def current() -> dict | None:
    path = lock_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"reason": "unknown", "started_at": None}


def is_locked() -> bool:
    return lock_path().exists()


def acquire(reason: str) -> None:
    path = lock_path()
    payload = json.dumps({
        "reason": reason, "pid": os.getpid(),
        "started_at": dt.datetime.now(dt.UTC).isoformat(),
    })
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        info = current() or {}
        raise MaintenanceLockError(
            f"Maintenance lock already held ({info.get('reason')}, since "
            f"{info.get('started_at')}). If no backup/restore is running, clear it with "
            "'manage.py maintenance --clear'."
        ) from exc
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(payload)


def release() -> None:
    with contextlib.suppress(FileNotFoundError):
        lock_path().unlink()


@contextlib.contextmanager
def write_freeze(reason: str, drain_seconds: float | None = None):
    """Hold the maintenance lock; wait briefly so in-flight writes finish."""
    acquire(reason)
    try:
        wait = getattr(settings, "MAINTENANCE_DRAIN_SECONDS", 2.0) if drain_seconds is None else drain_seconds
        if wait:
            time.sleep(wait)
        yield
    finally:
        release()
