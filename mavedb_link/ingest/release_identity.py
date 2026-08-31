"""Pure, bounded identity checks for immutable MaveDB data releases.

The publisher runs this module from the credential-free build artifact.  It only
reads local paths: it neither invokes a release client nor writes to a release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

MAX_METADATA_BYTES = 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_TAG_RE = re.compile(r"^data-\d{4}-\d{2}-\d{2}-s\d+(?:-r[1-9]\d*)?$")
_SCHEMA_RE = re.compile(r"^\d+\.0\.0$")
_SCHEMA_MAJOR_MAX = 999
_RELEASE_ASSETS = frozenset(
    {
        "mavedb.sqlite.zst",
        "mavedb.sqlite.zst.sha256",
        "bundle-metadata.json",
        "SHA256SUMS",
    }
)
_CHECKSUM_ASSETS = frozenset(_RELEASE_ASSETS - {"SHA256SUMS"})
_STABLE_FIELDS: dict[str, type[object]] = {
    "tag": str,
    "asset_sha256": str,
    "asset_size": int,
    "expanded_tree_sha256": str,
    "expanded_size": int,
    "schema_version": str,
    "build_revision": str,
    "source_sha256": str,
    "source_url": str,
    "score_set_count": int,
    "mapped_variant_count": int,
}
_ALL_METADATA_FIELDS = frozenset((*_STABLE_FIELDS, "retrieved_at"))


class IdentityComparisonError(ValueError):
    """The supplied release identity is missing, unsafe, or inconsistent."""


class ReleaseState(StrEnum):
    """The sole release state that controls publisher mutations."""

    PUBLISHED_NOOP = "published_noop"
    DRAFT_PUBLISH_EXISTING = "draft_publish_existing"
    CREATE = "create"
    COLLISION = "collision"


@dataclass(frozen=True)
class IdentityComparison:
    """The typed result of comparing a candidate to one release identity."""

    state: ReleaseState
    differing_field: str | None = None

    @property
    def exit_code(self) -> int:
        """Return a process status suitable for a fail-closed publisher."""
        return 1 if self.state is ReleaseState.COLLISION else 0


@dataclass(frozen=True)
class DatabaseIdentity:
    """Canonical identity fields read from the mirror's single metadata row."""

    schema_major: int
    schema_version: str
    dump_as_of: str | None
    source_sha256: str | None
    source_url: str | None
    score_set_count: int | None
    mapped_variant_count: int | None


def read_database_identity(database: Path) -> DatabaseIdentity:
    """Read and validate the canonical release identity from a built database."""
    if not database.is_file() or database.is_symlink():
        raise IdentityComparisonError("expanded database is missing or unsafe")
    try:
        connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
        try:
            row_count = connection.execute("SELECT COUNT(*) FROM meta").fetchone()[0]
            rows = connection.execute(
                "SELECT schema_version, dump_as_of, source_sha256, source_url, "
                "score_set_count, mapped_variant_count FROM meta WHERE id = 1"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise IdentityComparisonError("expanded database has no readable identity") from exc
    if row_count != 1 or len(rows) != 1:
        raise IdentityComparisonError("expanded database must contain exactly one meta row")
    schema_major, dump_as_of, source_sha256, source_url, score_set_count, mapped_count = rows[0]
    if type(schema_major) is not int or not 0 <= schema_major <= _SCHEMA_MAJOR_MAX:
        raise IdentityComparisonError("expanded database meta.schema_version is invalid")
    return DatabaseIdentity(
        schema_major=schema_major,
        schema_version=f"{schema_major}.0.0",
        dump_as_of=dump_as_of,
        source_sha256=source_sha256,
        source_url=source_url,
        score_set_count=score_set_count,
        mapped_variant_count=mapped_count,
    )


def compare_release_identity(
    current: Path, existing: Path, *, expected_tag: str
) -> IdentityComparison:
    """Compare exact stable identity fields; retrieval time is intentionally volatile."""
    _validate_expected_tag(expected_tag)
    candidate = _read_metadata(current)
    prior = _read_metadata(existing)
    if candidate["tag"] != expected_tag or prior["tag"] != expected_tag:
        return IdentityComparison(ReleaseState.COLLISION, "tag")
    for field in _STABLE_FIELDS:
        if candidate[field] != prior[field]:
            return IdentityComparison(ReleaseState.COLLISION, field)
    return IdentityComparison(ReleaseState.PUBLISHED_NOOP)


def decide_release_identity(
    current: Path,
    existing: Path | None,
    *,
    existing_is_draft: bool,
    expected_tag: str,
) -> IdentityComparison:
    """Select one disjoint mutation state without invoking an external command."""
    _validate_expected_tag(expected_tag)
    candidate = _read_metadata(current)
    if candidate["tag"] != expected_tag:
        return IdentityComparison(ReleaseState.COLLISION, "tag")
    if existing is None:
        return IdentityComparison(ReleaseState.CREATE)
    comparison = compare_release_identity(current, existing, expected_tag=expected_tag)
    if comparison.state is ReleaseState.COLLISION:
        return comparison
    state = (
        ReleaseState.DRAFT_PUBLISH_EXISTING if existing_is_draft else ReleaseState.PUBLISHED_NOOP
    )
    return IdentityComparison(state)


def inspect_release_inventory(
    inventory: Path, *, expected_tag: str
) -> dict[str, bool | int | None]:
    """Classify an exact tag in a bounded, minimal GitHub release projection."""
    _validate_expected_tag(expected_tag)
    body = _read_bounded(inventory, label="release inventory")
    lines = body.splitlines()
    if len(lines) > 1000:
        raise IdentityComparisonError("release inventory exceeds 1000 records")
    release_ids: set[int] = set()
    matches: list[int] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IdentityComparisonError("release inventory is not valid JSON Lines") from exc
        if not isinstance(entry, dict) or set(entry) != {"id", "tag_name"}:
            raise IdentityComparisonError("release inventory entry has invalid keys")
        release_id = entry["id"]
        tag_name = entry["tag_name"]
        if (
            not isinstance(tag_name, str)
            or type(release_id) is not int
            or release_id <= 0
            or release_id in release_ids
        ):
            raise IdentityComparisonError("release inventory entry has invalid identity")
        release_ids.add(release_id)
        if tag_name == expected_tag:
            matches.append(release_id)
    if len(matches) > 1:
        raise IdentityComparisonError("release inventory must contain at most one exact tag")
    release_id = matches[0] if matches else None
    return {"present": release_id is not None, "release_id": release_id}


def verify_release_assets(
    metadata_path: Path,
    release_dir: Path,
    expanded_database: Path,
    *,
    expected_tag: str,
) -> None:
    """Verify a downloaded release's exact assets and expanded SQLite identity.

    The caller supplies an already bounded, decompressed database.  This function
    is deliberately read-only so it is also safe to use for an existing draft or
    immutable published release before any release action is considered.
    """
    _validate_expected_tag(expected_tag)
    metadata = _read_metadata(metadata_path)
    if metadata["tag"] != expected_tag:
        raise IdentityComparisonError("release metadata does not match expected release tag")
    _require_exact_release_assets(release_dir)
    checksums = _read_checksums(release_dir / "SHA256SUMS")
    for name in _CHECKSUM_ASSETS:
        asset = release_dir / name
        if checksums[name] != _sha256_file(asset):
            raise IdentityComparisonError(f"checksum mismatch for {name}")
    bundle = release_dir / "mavedb.sqlite.zst"
    if metadata["asset_sha256"] != _sha256_file(bundle):
        raise IdentityComparisonError("asset_sha256 does not match mavedb.sqlite.zst")
    if metadata["asset_size"] != bundle.stat().st_size:
        raise IdentityComparisonError("asset_size does not match mavedb.sqlite.zst")
    _verify_expanded_database(metadata, expanded_database)


def _read_metadata(path: Path) -> dict[str, object]:
    body = _read_bounded(path, label="release metadata")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise IdentityComparisonError("release metadata is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise IdentityComparisonError("release metadata must be a JSON object")
    keys = frozenset(payload)
    missing = sorted(_ALL_METADATA_FIELDS - keys)
    extra = sorted(keys - _ALL_METADATA_FIELDS)
    if missing or extra:
        raise IdentityComparisonError(
            f"release metadata keys are invalid: missing={missing}, extra={extra}"
        )
    for field, expected_type in _STABLE_FIELDS.items():
        value = payload[field]
        if type(value) is not expected_type:  # bool is not an accepted integer identity.
            raise IdentityComparisonError(f"release metadata field {field} has an invalid type")
    retrieved_at = payload["retrieved_at"]
    if not isinstance(retrieved_at, str) or not retrieved_at:
        raise IdentityComparisonError("release metadata field retrieved_at has an invalid type")
    _validate_metadata_values(payload)
    return payload


def _validate_metadata_values(payload: dict[str, object]) -> None:
    for field in ("asset_sha256", "expanded_tree_sha256", "source_sha256"):
        value = str(payload[field])
        if _SHA256_RE.fullmatch(value) is None:
            raise IdentityComparisonError(f"release metadata field {field} is not a sha256 digest")
    for field in ("asset_size", "expanded_size"):
        if _integer_field(payload, field) < 0:
            raise IdentityComparisonError(f"release metadata field {field} must not be negative")
    for field in ("score_set_count", "mapped_variant_count"):
        if _integer_field(payload, field) < 0:
            raise IdentityComparisonError(f"release metadata field {field} must not be negative")
    if _REVISION_RE.fullmatch(str(payload["build_revision"])) is None:
        raise IdentityComparisonError("release metadata field build_revision is invalid")
    tag = str(payload["tag"])
    schema = str(payload["schema_version"])
    if _TAG_RE.fullmatch(tag) is None or _SCHEMA_RE.fullmatch(schema) is None:
        raise IdentityComparisonError("release metadata has an invalid tag or schema_version")
    source_url = urlparse(str(payload["source_url"]))
    if source_url.scheme != "https" or not source_url.netloc:
        raise IdentityComparisonError("release metadata field source_url must be an https URL")


def _validate_expected_tag(expected_tag: str) -> None:
    if _TAG_RE.fullmatch(expected_tag) is None:
        raise IdentityComparisonError("expected release tag is invalid")


def _integer_field(payload: dict[str, object], field: str) -> int:
    value = payload[field]
    if not isinstance(value, int) or isinstance(value, bool):
        raise IdentityComparisonError(f"release metadata field {field} has an invalid type")
    return value


def _require_exact_release_assets(release_dir: Path) -> None:
    if not release_dir.is_dir():
        raise IdentityComparisonError("release asset directory is missing")
    paths = list(release_dir.iterdir())
    names = {path.name for path in paths}
    if names != _RELEASE_ASSETS:
        raise IdentityComparisonError("release assets are missing, extra, or malformed")
    if any(not path.is_file() or path.is_symlink() for path in paths):
        raise IdentityComparisonError("release assets must be regular files")


def _read_checksums(path: Path) -> dict[str, str]:
    body = _read_bounded(path, label="release checksums")
    try:
        lines = body.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise IdentityComparisonError("release checksums must be ASCII") from exc
    checksums: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if match is None:
            raise IdentityComparisonError("release checksums contain an unsafe entry")
        digest, name = match.groups()
        if name not in _CHECKSUM_ASSETS or name in checksums:
            raise IdentityComparisonError("release checksums contain an unsafe entry")
        checksums[name] = digest
    if frozenset(checksums) != _CHECKSUM_ASSETS:
        raise IdentityComparisonError("release checksums are missing or extra")
    sidecar = path.parent / "mavedb.sqlite.zst.sha256"
    expected_sidecar = f"{checksums['mavedb.sqlite.zst']}  mavedb.sqlite.zst\n"
    if _read_bounded(sidecar, label="bundle checksum sidecar") != expected_sidecar.encode("ascii"):
        raise IdentityComparisonError("bundle checksum sidecar is invalid")
    return checksums


def _verify_expanded_database(metadata: dict[str, object], database: Path) -> None:
    if not database.is_file() or database.is_symlink():
        raise IdentityComparisonError("expanded database is missing or unsafe")
    if database.stat().st_size != metadata["expanded_size"]:
        raise IdentityComparisonError("expanded_size does not match mavedb.sqlite")
    if _expanded_tree_sha256(database) != metadata["expanded_tree_sha256"]:
        raise IdentityComparisonError("expanded_tree_sha256 does not match mavedb.sqlite")
    identity = read_database_identity(database)
    actual = {
        "schema_version": identity.schema_version,
        "source_sha256": identity.source_sha256,
        "source_url": identity.source_url,
        "score_set_count": identity.score_set_count,
        "mapped_variant_count": identity.mapped_variant_count,
    }
    for field, value in actual.items():
        if value != metadata[field]:
            raise IdentityComparisonError(f"expanded database {field} does not match metadata")


def _expanded_tree_sha256(path: Path) -> str:
    identity = f"mavedb.sqlite\0{0o444:04o}\0{path.stat().st_size}\0{_sha256_file(path)}\n"
    return hashlib.sha256(identity.encode()).hexdigest()


def _read_bounded(path: Path, *, label: str) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise IdentityComparisonError(f"{label} is missing or unsafe")
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise IdentityComparisonError(f"{label} exceeds {MAX_METADATA_BYTES} bytes")
    with path.open("rb") as handle:
        body = handle.read(MAX_METADATA_BYTES + 1)
    if len(body) > MAX_METADATA_BYTES:
        raise IdentityComparisonError(f"{label} exceeds {MAX_METADATA_BYTES} bytes")
    return body


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    compare = commands.add_parser("compare", help="Compare two bounded metadata documents.")
    compare.add_argument("--current", required=True, type=Path)
    compare.add_argument("--existing", type=Path)
    compare.add_argument("--existing-is-draft", action="store_true")
    compare.add_argument("--expected-tag", required=True)
    verify = commands.add_parser("verify-assets", help="Verify downloaded exact release assets.")
    verify.add_argument("--metadata", required=True, type=Path)
    verify.add_argument("--release-dir", required=True, type=Path)
    verify.add_argument("--expanded-database", required=True, type=Path)
    verify.add_argument("--expected-tag", required=True)
    inventory = commands.add_parser(
        "inspect-inventory", help="Inspect a bounded GitHub release inventory projection."
    )
    inventory.add_argument("--inventory", required=True, type=Path)
    inventory.add_argument("--expected-tag", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run a machine-readable identity comparison or an asset verification."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "compare":
            result = decide_release_identity(
                args.current,
                args.existing,
                existing_is_draft=args.existing_is_draft,
                expected_tag=args.expected_tag,
            )
            sys.stdout.write(
                json.dumps(asdict(result), default=lambda value: value.value, sort_keys=True)
            )
            sys.stdout.write("\n")
            return result.exit_code
        if args.command == "inspect-inventory":
            inventory_result = inspect_release_inventory(
                args.inventory, expected_tag=args.expected_tag
            )
            sys.stdout.write(json.dumps(inventory_result, sort_keys=True))
            sys.stdout.write("\n")
            return 0
        if args.command == "verify-assets":
            verify_release_assets(
                args.metadata,
                args.release_dir,
                args.expanded_database,
                expected_tag=args.expected_tag,
            )
    except IdentityComparisonError as exc:
        sys.stderr.write(json.dumps({"error": str(exc)}, sort_keys=True))
        sys.stderr.write("\n")
        return 1
    sys.stdout.write(json.dumps({"valid": True}, sort_keys=True))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
