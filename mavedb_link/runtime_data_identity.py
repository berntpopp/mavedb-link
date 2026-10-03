"""Verify the active MaveDB mirror and expose the fleet runtime identity v1."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import stat
from pathlib import Path
from typing import Any

from mavedb_link.ingest.bundle import _expanded_tree_sha256

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTITY_KEYS = {"release_tag", "compressed_sha256", "expanded_sha256", "schema_version"}


class RuntimeDataIdentityError(ValueError):
    """The active database cannot prove the configured immutable release identity."""


def _valid_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise RuntimeDataIdentityError(f"{label} is not a valid SHA-256")
    return value


def query_data_probe(database: Path) -> dict[str, Any]:
    """Return a deterministic read-only semantic probe of the actual mirror."""
    if database.is_symlink():
        raise RuntimeDataIdentityError("served database must not be a symlink")
    uri = f"file:{database.resolve(strict=True)}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
        try:
            meta = connection.execute("SELECT schema_version FROM meta WHERE id = 1").fetchall()
            if len(meta) != 1 or not isinstance(meta[0][0], int):
                raise RuntimeDataIdentityError("database has no unique schema identity")
            count_row = connection.execute("SELECT COUNT(*) FROM score_set").fetchone()
            first_row = connection.execute(
                "SELECT urn FROM score_set ORDER BY urn LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise RuntimeDataIdentityError("served database query failed") from exc
    if count_row is None or not isinstance(count_row[0], int) or count_row[0] < 1:
        raise RuntimeDataIdentityError("served database has no score-set records")
    if first_row is None or not isinstance(first_row[0], str):
        raise RuntimeDataIdentityError("served database has no deterministic score-set row")
    return {
        "data_schema_version": f"{meta[0][0]}.0.0",
        "record_count": count_row[0],
        "query_result_sha256": hashlib.sha256(first_row[0].encode("utf-8")).hexdigest(),
    }


def verify_runtime_data_identity(
    database: Path,
    *,
    reference_root: Path,
    expected_release_tag: str,
    expected_compressed_sha256: str,
    expected_expanded_sha256: str,
    expected_schema_version: str,
) -> dict[str, str]:
    """Hash the active served SQLite file and compare it with the pinned release."""
    expected_compressed = _valid_sha256(expected_compressed_sha256, "expected compressed digest")
    expected_expanded = _valid_sha256(expected_expanded_sha256, "expected expanded digest")
    if database.is_symlink():
        raise RuntimeDataIdentityError("served database must not be a symlink")
    if reference_root.is_symlink():
        raise RuntimeDataIdentityError("reference root must not be a symlink")
    try:
        root = reference_root.resolve(strict=True)
        active = (root / "current").resolve(strict=True)
        served = database.resolve(strict=True)
    except OSError as exc:
        raise RuntimeDataIdentityError("active materialized reference is missing") from exc
    if not root.is_dir() or active.parent != root or not active.is_dir():
        raise RuntimeDataIdentityError("active materialized reference is invalid")
    if served.parent != active or served.name != database.name:
        raise RuntimeDataIdentityError(
            "served database is outside the active materialized reference"
        )
    if stat.S_IMODE(served.stat().st_mode) != 0o444:
        raise RuntimeDataIdentityError("served database is not immutable read-only data")

    identity_path = active / "data-identity.json"
    if identity_path.is_symlink() or not identity_path.is_file():
        raise RuntimeDataIdentityError("materializer identity record is missing or aliased")
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeDataIdentityError("materializer identity record is invalid") from exc
    if not isinstance(identity, dict) or set(identity) != _IDENTITY_KEYS:
        raise RuntimeDataIdentityError("materializer identity record has an invalid shape")
    if identity.get("release_tag") != expected_release_tag:
        raise RuntimeDataIdentityError("materialized release tag mismatch")
    actual_release_tag = identity.get("release_tag")
    if not isinstance(actual_release_tag, str):
        raise RuntimeDataIdentityError("materialized release tag is invalid")
    if _valid_sha256(identity.get("compressed_sha256"), "materialized compressed digest") != (
        expected_compressed
    ):
        raise RuntimeDataIdentityError("materialized compressed digest mismatch")
    if identity.get("schema_version") != expected_schema_version:
        raise RuntimeDataIdentityError("materialized schema version mismatch")

    actual_expanded = _expanded_tree_sha256(served, served.name)
    if actual_expanded != identity.get("expanded_sha256"):
        raise RuntimeDataIdentityError("expanded database identity mismatch")
    if actual_expanded != expected_expanded:
        raise RuntimeDataIdentityError("expanded database does not match the pinned release")
    probe = query_data_probe(served)
    if probe["data_schema_version"] != expected_schema_version:
        raise RuntimeDataIdentityError("served database schema version mismatch")
    return {"release_tag": actual_release_tag, "digest": f"sha256:{actual_expanded}"}


def verify_settings_runtime_identity(settings: Any) -> dict[str, str] | None:
    """Verify a configured production mirror, or return ``None`` when unpinned."""
    mirror = settings.mirror
    pins = (
        mirror.bundle_release_tag,
        mirror.bundle_expected_sha256,
        mirror.bundle_expected_expanded_sha256,
        mirror.bundle_expected_schema_version,
    )
    present = tuple(bool(value) for value in pins)
    if not any(present):
        return None
    if not all(present):
        raise RuntimeDataIdentityError("configured data release identity is incomplete")
    if not mirror.enabled:
        return None
    return verify_runtime_data_identity(
        mirror.db_path,
        reference_root=mirror.reference_root,
        expected_release_tag=mirror.bundle_release_tag,
        expected_compressed_sha256=mirror.bundle_expected_sha256,
        expected_expanded_sha256=mirror.bundle_expected_expanded_sha256,
        expected_schema_version=mirror.bundle_expected_schema_version,
    )


def runtime_identity_envelope(
    expected_release_tag: str,
    expected_expanded_sha256: str,
    actual: dict[str, str],
) -> dict[str, Any]:
    """Build the closed fleet runtime-v1 health fragment."""
    expected = {
        "release_tag": expected_release_tag,
        "digest": f"sha256:{_valid_sha256(expected_expanded_sha256, 'expected expanded digest')}",
    }
    if actual != expected:
        raise RuntimeDataIdentityError("runtime data identity does not match the pinned release")
    return {
        "data_available": True,
        "release_identity": {
            "schema_version": 1,
            "data_identity": {"expected": expected, "actual": actual},
        },
    }


__all__ = [
    "RuntimeDataIdentityError",
    "query_data_probe",
    "runtime_identity_envelope",
    "verify_runtime_data_identity",
    "verify_settings_runtime_identity",
]
