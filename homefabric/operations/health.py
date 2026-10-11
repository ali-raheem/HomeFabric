import os
import uuid

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from homefabric.operations import maintenance_lock


def readiness(include_scheduler: bool = False) -> dict:
    checks = {}
    ok = True
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        checks["database"] = "ok"
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        checks["migrations"] = "ok" if not pending else f"{len(pending)} pending"
        ok &= not pending
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc.__class__.__name__}"
        ok = False
    try:
        probe = settings.STAGING_DIR / f".probe-{uuid.uuid4().hex}"
        probe.write_bytes(b"ok")
        probe.unlink()
        checks["data_dir"] = "ok"
    except OSError as exc:
        checks["data_dir"] = f"error: {exc.strerror}"
        ok = False
    checks["blob_dir"] = "ok" if os.access(settings.BLOB_DIR, os.W_OK) else "not writable"
    ok &= checks["blob_dir"] == "ok"
    checks["maintenance_lock"] = "held" if maintenance_lock.is_locked() else "free"
    if include_scheduler and checks["database"] == "ok":
        from homefabric.operations.jobs import scheduler_health

        health = scheduler_health()
        checks["scheduler"] = "stale" if health["stale"] else "ok"
        ok &= not health["stale"]
    return {"ok": bool(ok), "checks": checks}
