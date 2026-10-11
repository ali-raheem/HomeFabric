"""Full-instance backup archive: create and verify.

Archive layout (ZIP, see docs/backup-format.md):

    manifest.json      versioned manifest with checksums, counts, schema
    manifest.sha256    SHA-256 of manifest.json
    db.sqlite3         consistent, sanitised SQLite snapshot
    blobs/<key>        every referenced uploaded file revision
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import platform
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import django
from django.conf import settings
from django.db import connection

from homefabric import __version__
from homefabric.config import REQUIRED_SECRETS
from homefabric.documents import storage
from homefabric.operations import maintenance_lock

FORMAT = "homefabric-backup"
FORMAT_VERSION = 1
CHUNK = 1024 * 1024
MAX_RATIO = 200          # compression ratio above which an entry is treated as a bomb
SAFE_NAME = re.compile(r"^(manifest\.json|manifest\.sha256|db\.sqlite3|"
                       r"blobs/[0-9a-f]{32}/[0-9a-f]{2}/[0-9a-f]{32})$")

# Tables wiped from the snapshot: sessions, throttling and runtime locks.
WIPE_TABLES = ["django_session", "accounts_loginattempt", "operations_joblock"]


class BackupError(Exception):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def db_path() -> Path:
    if connection.vendor != "sqlite":
        raise BackupError("Backups currently support the SQLite database only.")
    return Path(connection.settings_dict["NAME"])


def applied_migrations(conn: sqlite3.Connection) -> dict[str, list[str]]:
    schema: dict[str, list[str]] = {}
    for app, name in conn.execute("SELECT app, name FROM django_migrations ORDER BY app, id"):
        schema.setdefault(app, []).append(name)
    return schema


def sanitise_snapshot(path: Path) -> dict:
    """Strip secrets, sessions and tokens; return table row counts."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        for table in WIPE_TABLES:
            conn.execute(f'DELETE FROM "{table}"')  # noqa: S608 - fixed table names
        now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M:%S")
        # Invalidate unused invitation tokens by replacing their hashes.
        for (pk,) in conn.execute(
            "SELECT id FROM accounts_invitation WHERE used_at IS NULL").fetchall():
            conn.execute("UPDATE accounts_invitation SET token_hash=?, revoked_at=? WHERE id=?",
                         (uuid.uuid4().hex + uuid.uuid4().hex, now, pk))
        # Provider credentials are never written to a backup.
        conn.execute(
            "UPDATE notifications_notificationendpoint SET needs_verification=1 "
            "WHERE secret_value != '' OR secret_ref != ''")
        conn.execute("UPDATE notifications_notificationendpoint SET secret_value=''")
        # Rotate session epochs so any copied cookie is useless.
        conn.execute("UPDATE accounts_user SET session_epoch = session_epoch + 1")
        conn.commit()
        conn.execute("VACUUM")  # purge freed pages that held removed secrets
        counts = {}
        for (table,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall():
            counts[table] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]  # noqa: S608
        return counts
    finally:
        conn.close()


def referenced_blobs(path: Path) -> list[tuple[str, str, int]]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT storage_key, sha256, size FROM documents_storedblob ORDER BY storage_key"
        ).fetchall()
    finally:
        conn.close()


def non_secret_config() -> dict:
    return {
        "public_base_url": settings.PUBLIC_BASE_URL,
        "default_timezone": settings.DEFAULT_HOUSEHOLD_TIMEZONE,
        "default_currency": settings.DEFAULT_HOUSEHOLD_CURRENCY,
        "schedule_horizon_days": settings.SCHEDULE_HORIZON_DAYS,
        "max_upload_bytes": settings.MAX_UPLOAD_BYTES,
        "notify_max_attempts": settings.NOTIFY_MAX_ATTEMPTS,
        "email_configured": settings.EMAIL_CONFIGURED,
        "channels": sorted(settings.NOTIFICATION_ADAPTERS),
    }


def _ensure_space(directory: Path, needed: int) -> None:
    free = shutil.disk_usage(directory).free
    if free < needed:
        raise BackupError(f"Not enough free disk space in {directory}: need about "
                          f"{needed // (1024 * 1024)} MiB, have {free // (1024 * 1024)} MiB.")


def create_backup(output, *, drain_seconds: float | None = None) -> dict:
    """Write a backup archive to ``output`` atomically and return its manifest."""
    output = Path(output).expanduser().resolve()
    if output.is_dir():
        output = output / f"homefabric-{dt.datetime.now():%Y%m%d-%H%M%S}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    blob_root = storage.blob_root().resolve()
    if blob_root in output.parents or settings.INSTANCE_DIR.resolve() in output.parents:
        raise BackupError("Write backups outside the instance data directory.")
    source = db_path()
    estimate = source.stat().st_size * 2 + sum(
        f.stat().st_size for _, f in storage.iter_blob_files())
    _ensure_space(output.parent, int(estimate * 1.1) + 50 * 1024 * 1024)
    _ensure_space(Path(settings.STAGING_DIR), source.stat().st_size * 2 + 10 * 1024 * 1024)

    partial = output.with_name(f".{output.name}.partial-{uuid.uuid4().hex[:8]}")
    workdir = Path(tempfile.mkdtemp(prefix="backup-", dir=settings.STAGING_DIR))
    try:
        with maintenance_lock.write_freeze("backup", drain_seconds=drain_seconds):
            snapshot = workdir / "db.sqlite3"
            src = sqlite3.connect(source, timeout=settings.SQLITE_BUSY_TIMEOUT_SECONDS)
            dst = sqlite3.connect(snapshot)
            try:
                src.backup(dst)  # SQLite online backup API: consistent snapshot
            finally:
                dst.close()
                src.close()
            counts = sanitise_snapshot(snapshot)
            conn = sqlite3.connect(snapshot)
            try:
                schema = applied_migrations(conn)
            finally:
                conn.close()
            blobs = referenced_blobs(snapshot)
            files = []
            with zipfile.ZipFile(partial, "w", allowZip64=True) as zf:
                zf.write(snapshot, "db.sqlite3", compress_type=zipfile.ZIP_DEFLATED)
                for key, sha, size in blobs:
                    path = storage.path_for(key)
                    if not path.exists():
                        raise BackupError(f"Referenced file {key} is missing; run "
                                          "check_integrity before backing up.")
                    actual = _sha256_file(path)
                    if actual != sha:
                        raise BackupError(f"File {key} failed its checksum; run "
                                          "check_integrity --verify-checksums.")
                    zf.write(path, f"blobs/{key}", compress_type=zipfile.ZIP_STORED)
                    files.append({"path": f"blobs/{key}", "sha256": sha, "size": size})
                manifest = {
                    "format": FORMAT,
                    "format_version": FORMAT_VERSION,
                    "app_version": __version__,
                    "created_at": dt.datetime.now(dt.UTC).isoformat(),
                    "source": {"platform": platform.platform(), "python": sys.version.split()[0],
                               "django": django.get_version()},
                    "schema": schema,
                    "database": {"path": "db.sqlite3", "sha256": _sha256_file(snapshot),
                                 "size": snapshot.stat().st_size},
                    "files": files,
                    "counts": counts,
                    "config": non_secret_config(),
                    "required_secrets": [{"name": n, "description": d}
                                         for n, d in REQUIRED_SECRETS],
                    "excluded": ["sessions", "invitation and reset tokens",
                                 "notification provider credentials", "secret key"],
                    "warning": "This archive contains every household's data and user password "
                               "hashes. Store it encrypted and access-controlled.",
                }
                manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode()
                zf.writestr("manifest.json", manifest_bytes, compress_type=zipfile.ZIP_DEFLATED)
                zf.writestr("manifest.sha256", hashlib.sha256(manifest_bytes).hexdigest())
        # Verify before replacing any previous archive at this path.
        result = verify_archive(partial)
        if not result.ok:
            raise BackupError("Backup failed verification: " + "; ".join(result.errors))
        os.replace(partial, output)
        if os.name == "posix":
            os.chmod(output, 0o600)
        manifest["archive"] = str(output)
        return manifest
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        partial.unlink(missing_ok=True)


@dataclass
class VerifyResult:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manifest: dict | None = None
    uncompressed_size: int = 0

    def fail(self, message: str):
        self.ok = False
        self.errors.append(message)


def known_migrations() -> dict[str, set[str]]:
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(None, ignore_no_migrations=True)
    known: dict[str, set[str]] = {}
    for app, name in loader.disk_migrations:
        known.setdefault(app, set()).add(name)
    return known


def _copy_bounded(src, dest_fh, expected_size: int) -> str:
    digest = hashlib.sha256()
    total = 0
    while chunk := src.read(CHUNK):
        total += len(chunk)
        if total > expected_size:
            raise BackupError("Entry is larger than declared (possible decompression bomb).")
        digest.update(chunk)
        if dest_fh is not None:
            dest_fh.write(chunk)
    if total != expected_size:
        raise BackupError("Entry size does not match the manifest.")
    return digest.hexdigest()


def verify_archive(path, *, extract_to: Path | None = None,
                   max_total_bytes: int | None = None) -> VerifyResult:
    """Validate structure, safety, checksums and schema without touching live data.

    When ``extract_to`` is given, verified files are written beneath it.
    """
    result = VerifyResult()
    path = Path(path)
    if not path.is_file():
        result.fail(f"Archive not found: {path}")
        return result
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        result.fail(f"Not a valid archive: {exc}")
        return result
    with zf:
        infos = zf.infolist()
        names = set()
        for info in infos:
            name = info.filename
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                result.fail(f"Archive contains a symbolic link: {name}")
            if not SAFE_NAME.match(name) or ".." in name or name.startswith("/") or "\\" in name:
                result.fail(f"Archive contains an unexpected or unsafe path: {name!r}")
            if name in names:
                result.fail(f"Archive contains a duplicate entry: {name}")
            names.add(name)
            if info.compress_size and info.file_size / max(info.compress_size, 1) > MAX_RATIO \
                    and info.file_size > 10 * 1024 * 1024:
                result.fail(f"Entry {name} has a suspicious compression ratio.")
            result.uncompressed_size += info.file_size
        if not result.ok:
            return result
        if max_total_bytes is not None and result.uncompressed_size > max_total_bytes:
            result.fail("Archive is larger than the allowed restore size.")
            return result
        for required in ("manifest.json", "manifest.sha256", "db.sqlite3"):
            if required not in names:
                result.fail(f"Archive is missing {required}.")
        if not result.ok:
            return result
        if zf.getinfo("manifest.json").file_size > 50 * 1024 * 1024:
            result.fail("Manifest is unreasonably large.")
            return result
        manifest_bytes = zf.read("manifest.json")
        expected = zf.read("manifest.sha256").decode("ascii", "replace").strip()
        if hashlib.sha256(manifest_bytes).hexdigest() != expected:
            result.fail("Manifest checksum mismatch; the archive is corrupt or was modified.")
            return result
        try:
            manifest = json.loads(manifest_bytes)
        except ValueError:
            result.fail("Manifest is not valid JSON.")
            return result
        result.manifest = manifest
        if manifest.get("format") != FORMAT:
            result.fail("Not a HomeFabric backup archive.")
            return result
        if not isinstance(manifest.get("format_version"), int) or \
                manifest["format_version"] > FORMAT_VERSION:
            result.fail(f"Backup format version {manifest.get('format_version')} is newer than "
                        f"this release supports ({FORMAT_VERSION}). Upgrade HomeFabric first.")
            return result
        known = known_migrations()
        for app, migrations in (manifest.get("schema") or {}).items():
            unknown = [m for m in migrations if m not in known.get(app, set())]
            if unknown and app in {"accounts", "core", "assets", "documents", "maintenance",
                                   "notifications", "operations", "auth", "contenttypes",
                                   "sessions", "admin"}:
                result.fail(f"Backup schema is newer than this release ({app}: "
                            f"{', '.join(unknown[:3])}). Install a compatible or newer release.")
        if not result.ok:
            return result
        declared = {f["path"]: f for f in manifest.get("files", [])}
        db_meta = manifest.get("database") or {}
        declared["db.sqlite3"] = {"path": "db.sqlite3", "sha256": db_meta.get("sha256"),
                                  "size": db_meta.get("size")}
        actual_entries = names - {"manifest.json", "manifest.sha256"}
        if set(declared) != actual_entries:
            missing = set(declared) - actual_entries
            extra = actual_entries - set(declared)
            if missing:
                result.fail(f"Archive is missing {len(missing)} declared file(s).")
            if extra:
                result.fail(f"Archive contains {len(extra)} undeclared file(s).")
            return result
        tmp_db = None
        for name, meta in declared.items():
            if not isinstance(meta.get("size"), int) or zf.getinfo(name).file_size != meta["size"]:
                result.fail(f"{name}: size does not match the manifest.")
                continue
            dest = None
            if extract_to is not None:
                dest = Path(extract_to) / name
            elif name == "db.sqlite3":
                tmp_db = Path(tempfile.mkdtemp(prefix="verify-", dir=settings.STAGING_DIR))
                dest = tmp_db / "db.sqlite3"
            try:
                if dest is not None:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(name) as src, open(dest, "wb") as out:
                        sha = _copy_bounded(src, out, meta["size"])
                else:
                    with zf.open(name) as src:
                        sha = _copy_bounded(src, None, meta["size"])
            except (BackupError, zipfile.BadZipFile, OSError, EOFError) as exc:
                result.fail(f"{name}: {exc}")
                continue
            if sha != meta.get("sha256"):
                result.fail(f"{name}: checksum mismatch.")
        db_file = (Path(extract_to) / "db.sqlite3") if extract_to else (
            tmp_db / "db.sqlite3" if tmp_db else None)
        try:
            if result.ok and db_file is not None:
                _verify_database(db_file, declared, result)
        finally:
            if tmp_db is not None:
                shutil.rmtree(tmp_db, ignore_errors=True)
    return result


def _verify_database(db_file: Path, declared: dict, result: VerifyResult) -> None:
    try:
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        result.fail(f"Database cannot be opened: {exc}")
        return
    try:
        check = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
        if check != ["ok"]:
            result.fail("Database integrity check failed: " + "; ".join(check[:5]))
            return
        keys = {f"blobs/{k}" for (k,) in conn.execute(
            "SELECT storage_key FROM documents_storedblob").fetchall()}
        missing = keys - set(declared)
        if missing:
            result.fail(f"{len(missing)} document file(s) referenced by the database are "
                        "missing from the archive.")
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            result.fail(f"Database has {len(fk)} broken reference(s).")
    except sqlite3.DatabaseError as exc:
        result.fail(f"Database is not readable: {exc}")
    finally:
        conn.close()
