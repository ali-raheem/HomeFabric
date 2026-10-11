"""Instance configuration.

Values come from environment variables prefixed ``FABRIC_`` or from a TOML
configuration file named by ``FABRIC_CONFIG_FILE``. Environment variables win.
Any key may also be supplied as ``FABRIC_<KEY>_FILE`` pointing at a file that
contains the value, so secrets never need to appear in process listings.
"""

from __future__ import annotations

import os
import secrets
import stat
import sys
import tomllib
import warnings
from pathlib import Path

PREFIX = "FABRIC_"


class ConfigError(Exception):
    pass


def _default_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "HomeFabric"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "HomeFabric"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "homefabric"


def _load_file_config() -> dict:
    path = os.environ.get(PREFIX + "CONFIG_FILE")
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"Configuration file not found: {p}")
    if os.name == "posix":
        mode = p.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            warnings.warn(
                f"Configuration file {p} is readable by group/others; restrict it with chmod 600.",
                stacklevel=2,
            )
    with p.open("rb") as fh:
        data = tomllib.load(fh)
    return {str(k).upper(): v for k, v in data.items()}


class Config:
    def __init__(self, environ: dict | None = None):
        self.environ = os.environ if environ is None else environ
        self.file_values = _load_file_config() if environ is None else {}

    def raw(self, key: str):
        env_key = PREFIX + key
        if env_key in self.environ:
            return self.environ[env_key]
        file_key = env_key + "_FILE"
        if file_key in self.environ:
            return Path(self.environ[file_key]).read_text(encoding="utf-8").strip()
        return self.file_values.get(key)

    def str(self, key: str, default: str | None = None) -> str | None:
        value = self.raw(key)
        return default if value is None or value == "" else str(value)

    def bool(self, key: str, default: bool = False) -> bool:
        value = self.raw(key)
        if value is None or value == "":
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def int(self, key: str, default: int) -> int:
        value = self.raw(key)
        if value is None or value == "":
            return default
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{PREFIX}{key} must be an integer") from exc

    def list(self, key: str, default: list[str] | None = None) -> list[str]:
        value = self.raw(key)
        if value is None or value == "":
            return list(default or [])
        if isinstance(value, list):
            return [str(v) for v in value]
        return [v.strip() for v in str(value).split(",") if v.strip()]


def data_dir(config: Config) -> Path:
    return Path(config.str("DATA_DIR") or _default_data_dir()).expanduser().resolve()


def load_or_create_secret_key(config: Config, directory: Path) -> str:
    key = config.str("SECRET_KEY")
    if key:
        return key
    key_file = directory / "secret_key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    directory.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(64)
    fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(key)
    return key


# Keys an operator must re-supply after restoring a backup on a new host.
# Values are never written to backups.
REQUIRED_SECRETS = [
    ("FABRIC_SECRET_KEY", "Django secret key (auto-generated in DATA_DIR/secret_key if unset)"),
    ("FABRIC_EMAIL_HOST_PASSWORD", "SMTP password, if SMTP authentication is used"),
    ("Apprise endpoint URLs", "Each user's Apprise notification URL must be re-entered"),
]
