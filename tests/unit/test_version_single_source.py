"""Guard: pyproject -> installed metadata -> __version__ -> serverInfo are one value."""

from __future__ import annotations

import re
import tomllib
from importlib.metadata import version
from pathlib import Path

import yaml

from mavedb_link import __version__
from mavedb_link.mcp.facade import create_mavedb_mcp

DIST = "mavedb-link"


def _pyproject_version() -> str:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


def test_pyproject_is_the_single_source() -> None:
    assert version(DIST) == _pyproject_version()


def test_dunder_version_is_metadata_derived() -> None:
    assert __version__ == version(DIST)


def test_mcp_server_info_version_matches_package() -> None:
    assert create_mavedb_mcp().version == __version__


def test_citation_matches_current_changelog_release() -> None:
    root = Path(__file__).resolve().parents[2]
    current = _pyproject_version()
    citation = yaml.safe_load((root / "CITATION.cff").read_text(encoding="utf-8"))
    release = re.search(
        rf"^## \[{re.escape(current)}\] - (\d{{4}}-\d{{2}}-\d{{2}})$",
        (root / "CHANGELOG.md").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert release is not None
    assert citation["version"] == current
    assert citation["date-released"] == release.group(1)
