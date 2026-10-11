"""Release bookkeeping: one version, semantic, and recorded in the changelog."""

import re
import tomllib
from pathlib import Path

from homefabric import __version__

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?$")


def test_version_is_semantic():
    assert SEMVER.match(__version__), __version__


def test_pyproject_reads_version_from_package():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" not in data["project"] and "version" in data["project"]["dynamic"]
    assert data["tool"]["setuptools"]["dynamic"]["version"]["attr"] == "homefabric.__version__"


def test_changelog_top_entry_matches_version():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    versions = re.findall(r"^## \[([^\]]+)\]", changelog, flags=re.M)
    assert versions and versions[0] == __version__, (
        "Bump homefabric.__version__ and add a CHANGELOG.md entry together.")
    as_tuples = [tuple(int(x) for x in v.split("-")[0].split(".")) for v in versions]
    assert as_tuples == sorted(as_tuples, reverse=True)
