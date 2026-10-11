"""Private blob storage under BLOB_DIR with opaque, traversal-proof names."""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import uuid
from pathlib import Path

from django.conf import settings

CHUNK = 1024 * 1024


def blob_root() -> Path:
    return Path(settings.BLOB_DIR)


def new_storage_key(household_id) -> str:
    key = uuid.uuid4().hex
    return f"{uuid.UUID(str(household_id)).hex}/{key[:2]}/{key}"


def path_for(storage_key: str) -> Path:
    root = blob_root().resolve()
    parts = storage_key.split("/")
    if len(parts) != 3 or not all(p.isalnum() for p in parts):
        raise ValueError("Invalid storage key")
    path = (root / parts[0] / parts[1] / parts[2]).resolve()
    if root not in path.parents:
        raise ValueError("Invalid storage key")
    return path


def hash_file(path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def stage_upload(uploaded_file) -> Path:
    """Copy an uploaded file into the staging directory and return its path."""
    staging = Path(settings.STAGING_DIR)
    staging.mkdir(parents=True, exist_ok=True)
    dest = staging / f"upload-{uuid.uuid4().hex}"
    with open(dest, "wb") as out:
        for chunk in uploaded_file.chunks(CHUNK):
            out.write(chunk)
    return dest


def commit_blob(staged: Path, storage_key: str) -> Path:
    """Move a staged file into place. Called before the DB transaction commits;
    if the DB write then fails the file is an orphan reported by check_integrity."""
    dest = path_for(storage_key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(staged, dest)
    except OSError:
        shutil.copy2(staged, dest)
        staged.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(dest, 0o600)
    return dest


def discard(staged: Path | None) -> None:
    if staged is not None:
        with contextlib.suppress(OSError):
            Path(staged).unlink(missing_ok=True)


def iter_blob_files():
    root = blob_root()
    if not root.exists():
        return
    for hh in root.iterdir():
        if not hh.is_dir():
            continue
        for prefix in hh.iterdir():
            if not prefix.is_dir():
                continue
            for f in prefix.iterdir():
                if f.is_file():
                    yield f"{hh.name}/{prefix.name}/{f.name}", f
