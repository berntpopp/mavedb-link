"""Fail-closed identity contract for the immutable MaveDB data release."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from mavedb_link.ingest.release_identity import (
    MAX_METADATA_BYTES,
    DatabaseIdentity,
    IdentityComparisonError,
    ReleaseState,
    compare_release_identity,
    decide_release_identity,
    read_database_identity,
    verify_release_assets,
)

ROOT = Path(__file__).resolve().parents[2]


def _workflow_steps() -> list[dict[str, object]]:
    workflow = yaml.safe_load((ROOT / ".github/workflows/data.yml").read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    publish = workflow["jobs"]["publish"]
    assert isinstance(publish, dict)
    steps = publish["steps"]
    assert isinstance(steps, list)
    return steps


def _workflow_step(name: str) -> dict[str, object]:
    return next(step for step in _workflow_steps() if step.get("name") == name)


def _metadata(**overrides: object) -> dict[str, object]:
    """Return a complete, internally consistent stable metadata fixture."""
    payload: dict[str, object] = {
        "tag": "data-2026-06-24-s0",
        "asset_sha256": "a" * 64,
        "asset_size": 12,
        "expanded_tree_sha256": "b" * 64,
        "expanded_size": 8192,
        "schema_version": "0.0.0",
        "build_revision": "d" * 40,
        "source_sha256": "c" * 64,
        "source_url": "https://zenodo.org/records/11201736/files/mavedb.zip",
        "retrieved_at": "2026-08-30T12:00:00Z",
        "score_set_count": 4,
        "mapped_variant_count": 8,
    }
    payload.update(overrides)
    return payload


def _write_metadata(path: Path, **overrides: object) -> Path:
    path.write_text(json.dumps(_metadata(**overrides)), encoding="utf-8")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_database(path: Path, metadata: dict[str, object]) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA user_version = 0")
        connection.execute(
            """CREATE TABLE meta (
                id INTEGER PRIMARY KEY,
                schema_version INTEGER,
                dump_as_of TEXT,
                source_sha256 TEXT,
                source_url TEXT,
                score_set_count INTEGER,
                mapped_variant_count INTEGER
            )"""
        )
        connection.execute(
            "INSERT INTO meta VALUES (1, ?, ?, ?, ?, ?, ?)",
            (
                int(str(metadata["schema_version"]).split(".", 1)[0]),
                "2026-06-24T00:00:00+00:00",
                metadata["source_sha256"],
                metadata["source_url"],
                metadata["score_set_count"],
                metadata["mapped_variant_count"],
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _tree_sha256(path: Path) -> str:
    file_sha256 = _sha256(path)
    identity = f"mavedb.sqlite\0{0o444:04o}\0{path.stat().st_size}\0{file_sha256}\n"
    return hashlib.sha256(identity.encode()).hexdigest()


def _release_directory(tmp_path: Path) -> tuple[Path, Path]:
    release = tmp_path / "release"
    release.mkdir()
    bundle = release / "mavedb.sqlite.zst"
    bundle.write_bytes(b"sealed-bundle")
    database = tmp_path / "mavedb.sqlite"
    metadata = _metadata(asset_sha256=_sha256(bundle), asset_size=bundle.stat().st_size)
    _write_database(database, metadata)
    metadata["expanded_size"] = database.stat().st_size
    metadata["expanded_tree_sha256"] = _tree_sha256(database)
    _write_metadata(release / "bundle-metadata.json", **metadata)
    (release / "mavedb.sqlite.zst.sha256").write_text(
        f"{metadata['asset_sha256']}  mavedb.sqlite.zst\n", encoding="ascii"
    )
    checksums = "\n".join(
        f"{_sha256(release / name)}  {name}"
        for name in (
            "mavedb.sqlite.zst",
            "mavedb.sqlite.zst.sha256",
            "bundle-metadata.json",
        )
    )
    (release / "SHA256SUMS").write_text(f"{checksums}\n", encoding="ascii")
    return release, database


def test_identical_stable_identity_ignores_retrieval_time(tmp_path: Path) -> None:
    current = _write_metadata(tmp_path / "current.json", retrieved_at="2026-08-30T00:00:00Z")
    existing = _write_metadata(tmp_path / "existing.json", retrieved_at="2026-08-31T00:00:00Z")

    result = compare_release_identity(current, existing, expected_tag="data-2026-06-24-s0")

    assert result.state is ReleaseState.PUBLISHED_NOOP
    assert result.differing_field is None


def test_candidate_and_existing_metadata_must_match_the_requested_release_tag(
    tmp_path: Path,
) -> None:
    requested = "data-2026-06-24-s4-r2"
    substituted = "data-2026-06-25-s4-r2"
    current = _write_metadata(tmp_path / "current.json", tag=substituted)
    existing = _write_metadata(tmp_path / "existing.json", tag=substituted)

    result = decide_release_identity(
        current,
        existing,
        existing_is_draft=False,
        expected_tag=requested,
    )

    assert result.state is ReleaseState.COLLISION
    assert result.differing_field == "tag"


def test_asset_verifier_rejects_metadata_for_another_release_tag(tmp_path: Path) -> None:
    release, database = _release_directory(tmp_path)

    with pytest.raises(IdentityComparisonError, match="expected release tag"):
        verify_release_assets(
            release / "bundle-metadata.json",
            release,
            database,
            expected_tag="data-2026-06-25-s0-r2",
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_sha256", "d" * 64),
        ("asset_sha256", "d" * 64),
        ("asset_size", 13),
        ("expanded_tree_sha256", "d" * 64),
        ("expanded_size", 8193),
        ("schema_version", "1.0.0"),
        ("build_revision", "e" * 40),
        ("score_set_count", 5),
        ("mapped_variant_count", 9),
    ],
)
def test_identity_difference_is_a_named_nonzero_collision(
    tmp_path: Path, field: str, value: object
) -> None:
    current = _write_metadata(tmp_path / "current.json")
    existing = _write_metadata(tmp_path / "existing.json", **{field: value})

    result = compare_release_identity(current, existing, expected_tag="data-2026-06-24-s0")

    assert result.state is ReleaseState.COLLISION
    assert result.differing_field == field
    assert result.exit_code == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"tag": "data-2026-06-24-s0"},
        {**_metadata(), "unexpected": "value"},
        _metadata(asset_size=True),
        _metadata(source_sha256="not-a-digest"),
        _metadata(build_revision="not-a-revision"),
    ],
)
def test_identity_metadata_rejects_missing_extra_or_invalid_typed_fields(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    current = tmp_path / "current.json"
    existing = tmp_path / "existing.json"
    current.write_text(json.dumps(payload), encoding="utf-8")
    _write_metadata(existing)

    with pytest.raises(IdentityComparisonError):
        compare_release_identity(current, existing, expected_tag="data-2026-06-24-s0")


@pytest.mark.parametrize(
    "mutator",
    [
        lambda release, database: (release / "unexpected.txt").write_text("no", encoding="utf-8"),
        lambda release, database: (release / "SHA256SUMS").write_text(
            f"{'0' * 64}  ../unsafe\n", encoding="ascii"
        ),
        lambda release, database: (release / "mavedb.sqlite.zst").write_bytes(b"corrupt"),
        lambda release, database: (release / "bundle-metadata.json").unlink(),
        lambda release, database: database.write_bytes(b"not sqlite"),
    ],
)
def test_release_asset_verification_fails_closed_for_tampering(
    tmp_path: Path, mutator: object
) -> None:
    release, database = _release_directory(tmp_path)
    assert callable(mutator)
    mutator(release, database)

    with pytest.raises(IdentityComparisonError):
        verify_release_assets(
            release / "bundle-metadata.json",
            release,
            database,
            expected_tag="data-2026-06-24-s0",
        )


def test_asset_verifier_does_not_modify_candidate_or_existing_inputs(tmp_path: Path) -> None:
    release, database = _release_directory(tmp_path)
    before = {path: path.read_bytes() for path in [*release.iterdir(), database]}

    verify_release_assets(
        release / "bundle-metadata.json",
        release,
        database,
        expected_tag="data-2026-06-24-s0",
    )

    assert {path: path.read_bytes() for path in before} == before


def test_cli_reports_machine_readable_create_and_draft_states(tmp_path: Path) -> None:
    current = _write_metadata(tmp_path / "current.json")
    existing = _write_metadata(tmp_path / "existing.json")
    command = [
        sys.executable,
        "-m",
        "mavedb_link.ingest.release_identity",
        "compare",
        "--current",
        str(current),
        "--expected-tag",
        "data-2026-06-24-s0",
    ]

    create = subprocess.run(command, check=False, capture_output=True, text=True)  # noqa: S603
    draft = subprocess.run(  # noqa: S603
        [*command, "--existing", str(existing), "--existing-is-draft"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert create.returncode == 0
    assert json.loads(create.stdout) == {"differing_field": None, "state": "create"}
    assert draft.returncode == 0
    assert json.loads(draft.stdout) == {
        "differing_field": None,
        "state": "draft_publish_existing",
    }


def test_release_decision_states_are_disjoint(tmp_path: Path) -> None:
    current = _write_metadata(tmp_path / "current.json")
    existing = _write_metadata(tmp_path / "existing.json")
    collision = _write_metadata(tmp_path / "collision.json", source_url="https://example.test/new")

    assert (
        decide_release_identity(
            current,
            None,
            existing_is_draft=False,
            expected_tag="data-2026-06-24-s0",
        ).state
        is ReleaseState.CREATE
    )
    assert (
        decide_release_identity(
            current,
            existing,
            existing_is_draft=False,
            expected_tag="data-2026-06-24-s0",
        ).state
        is ReleaseState.PUBLISHED_NOOP
    )
    assert (
        decide_release_identity(
            current,
            existing,
            existing_is_draft=True,
            expected_tag="data-2026-06-24-s0",
        ).state
        is ReleaseState.DRAFT_PUBLISH_EXISTING
    )
    assert (
        decide_release_identity(
            current,
            collision,
            existing_is_draft=True,
            expected_tag="data-2026-06-24-s0",
        ).state
        is ReleaseState.COLLISION
    )


def test_metadata_reader_rejects_more_than_one_mebibyte(tmp_path: Path) -> None:
    current = tmp_path / "current.json"
    current.write_bytes(b"{" + b" " * MAX_METADATA_BYTES + b"}")
    existing = _write_metadata(tmp_path / "existing.json")

    with pytest.raises(IdentityComparisonError, match="exceeds"):
        compare_release_identity(current, existing, expected_tag="data-2026-06-24-s0")


def test_database_identity_uses_meta_schema_not_pragma(tmp_path: Path) -> None:
    metadata = _metadata(tag="data-2026-06-24-s4", schema_version="4.0.0")
    database = tmp_path / "mavedb.sqlite"
    _write_database(database, metadata)

    identity = read_database_identity(database)

    assert isinstance(identity, DatabaseIdentity)
    assert identity.schema_major == 4
    assert identity.schema_version == "4.0.0"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


@pytest.mark.parametrize("schema_version", [None, "not-an-int", -1, 1000])
def test_database_identity_rejects_invalid_meta_schema_version(
    tmp_path: Path, schema_version: object
) -> None:
    metadata = _metadata(tag="data-2026-06-24-s4", schema_version="4.0.0")
    database = tmp_path / "mavedb.sqlite"
    _write_database(database, metadata)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE meta SET schema_version = ?", (schema_version,))
        connection.commit()

    with pytest.raises(IdentityComparisonError, match="schema_version"):
        read_database_identity(database)


def test_database_identity_rejects_multiple_meta_rows(tmp_path: Path) -> None:
    metadata = _metadata(tag="data-2026-06-24-s4", schema_version="4.0.0")
    database = tmp_path / "mavedb.sqlite"
    _write_database(database, metadata)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO meta VALUES (2, 4, ?, ?, ?, ?, ?)",
            (
                "2026-06-24T00:00:00+00:00",
                metadata["source_sha256"],
                metadata["source_url"],
                metadata["score_set_count"],
                metadata["mapped_variant_count"],
            ),
        )
        connection.commit()

    with pytest.raises(IdentityComparisonError, match="exactly one"):
        read_database_identity(database)


def test_data_workflow_metadata_uses_canonical_identity_reader() -> None:
    build_metadata = _workflow_step_from_job("build", "Create exact release metadata")
    script = str(build_metadata["run"])

    assert "read_database_identity" in script
    assert "PRAGMA user_version" not in script


def test_workflow_metadata_script_uses_meta_schema_from_authentic_build(tmp_path: Path) -> None:
    from mavedb_link.ingest.builder import build_database
    from tests.dump_fixture import write_mini_dump

    data = tmp_path / "data"
    data.mkdir()
    dump_dir = tmp_path / "dump"
    dump_dir.mkdir()
    build_database(
        write_mini_dump(dump_dir),
        data / "mavedb.sqlite",
        source_sha256="c" * 64,
        source_url="https://zenodo.org/records/20840937/files/mavedb.zip",
    )
    (data / "mavedb.sqlite.zst").write_bytes(b"sealed-bundle")
    build_metadata = _workflow_step_from_job("build", "Create exact release metadata")
    workflow_script = str(build_metadata["run"])
    python_script = workflow_script.split("uv run python - <<'PY'\n", 1)[1].split("\nPY", 1)[0]

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", python_script],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT), "GITHUB_SHA": "d" * 40},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads((data / "bundle-metadata.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == "4.0.0"
    assert payload["tag"] == "data-2026-02-06-s4-r3"
    assert payload["build_revision"] == "d" * 40


def _workflow_step_from_job(job_name: str, step_name: str) -> dict[str, object]:
    workflow = yaml.safe_load((ROOT / ".github/workflows/data.yml").read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    job = workflow["jobs"][job_name]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    return next(step for step in steps if step.get("name") == step_name)


def test_data_workflow_has_four_explicit_non_destructive_identity_gates() -> None:
    """Publisher effects are reachable only from their one typed release state."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/data.yml").read_text(encoding="utf-8"))
    assert workflow["jobs"]["publish"]["if"] == "github.ref == 'refs/heads/main'"
    inspect_existing = _workflow_step("Inspect and verify an existing same-tag release")
    inspect_tag = _workflow_step("Inspect exact Git tag target")
    decide = _workflow_step("Resolve the exact release identity state")
    inventory = _workflow_step("Inventory exact release identity")
    create_tag = _workflow_step("Create exact Git tag for a new identity")
    create = _workflow_step("Create an empty draft for a new identity")
    upload = _workflow_step("Upload new sealed assets")
    attest = _workflow_step("Attest new release assets")
    promote = _workflow_step("Promote a freshly verified exact draft")

    inspect_script = str(inspect_existing["run"])
    assert "gh api --include" in inspect_script
    assert "404" not in inspect_script
    assert "gh release download" not in inspect_script
    assert "releases/assets/$remote_id" in inspect_script
    assert '--max-filesize "$remote_size"' in inspect_script
    assert 'test "$remote_size" -le "$max_size"' in inspect_script
    assert "remote_id" in inspect_script
    assert "remote_size" in inspect_script
    assert "remote_digest" in inspect_script
    assert "gh attestation verify" in inspect_script
    assert '--signer-workflow "$GITHUB_REPOSITORY/.github/workflows/data.yml"' in inspect_script
    assert "--source-ref refs/heads/main" in inspect_script
    assert '--source-digest "$expected_build"' in inspect_script
    assert "|| true" not in inspect_script
    assert "gh release delete" not in inspect_script
    assert "verify-assets" in inspect_script
    assert '--expected-tag "$TAG"' in inspect_script
    tag_script = str(inspect_tag["run"])
    assert "git/ref/tags/$TAG" in tag_script
    assert "bundle-metadata.json" in tag_script
    assert "build_revision" in tag_script
    assert "404" in tag_script
    assert "|| true" not in tag_script
    assert "compare" in str(decide["run"])
    assert "release_identity.py" in str(decide["run"])
    assert '--expected-tag "$TAG"' in str(decide["run"])
    assert inventory.get("if") is None
    inventory_script = str(inventory["run"])
    assert "releases?per_page=100&page=$page" in inventory_script
    assert "seq 1 10" in inventory_script
    assert 'test "$complete" = true' in inventory_script
    assert "inspect-inventory" in inventory_script
    assert 'echo "present=$present"' in inventory_script
    assert 'echo "release_id=$release_id"' in inventory_script
    assert create_tag["if"] == (
        "steps.decision.outputs.state == 'create' && "
        "steps.tag_ref.outputs.present != 'true' && steps.inventory.outputs.present != 'true'"
    )
    create_tag_script = str(create_tag["run"])
    assert "git/refs" in create_tag_script
    assert 'ref="refs/tags/$TAG"' in create_tag_script
    assert "--method POST" in create_tag_script
    assert "bundle-metadata.json" in create_tag_script
    assert "build_revision" in create_tag_script
    names = [str(step.get("name", "")) for step in _workflow_steps()]
    assert names.index("Inventory exact release identity") < names.index(
        "Inspect and verify an existing same-tag release"
    )
    assert names.index("Inspect and verify an existing same-tag release") < names.index(
        "Resolve the exact release identity state"
    )
    assert names.index("Resolve the exact release identity state") < names.index(
        "Create exact Git tag for a new identity"
    )
    assert names.index("Create exact Git tag for a new identity") < names.index(
        "Create an empty draft for a new identity"
    )
    assert create["if"] == "steps.decision.outputs.state == 'create'"
    assert 'target_commitish="$BUILD_REVISION"' in str(create["run"])
    assert upload["if"] == "steps.decision.outputs.state == 'create'"
    assert attest["if"] == "steps.decision.outputs.state == 'create'"
    assert promote["if"] == (
        "steps.decision.outputs.state == 'draft_publish_existing' || "
        "steps.decision.outputs.state == 'create'"
    )
    promote_script = str(promote["run"])
    assert "gh api" in promote_script
    assert "gh release download" not in promote_script
    assert "releases/assets/$remote_id" in promote_script
    assert '--max-filesize "$remote_size"' in promote_script
    assert 'test "$remote_size" -le "$max_size"' in promote_script
    assert "remote_size" in promote_script
    assert "remote_digest" in promote_script
    assert "gh attestation verify" in promote_script
    assert '--signer-workflow "$GITHUB_REPOSITORY/.github/workflows/data.yml"' in promote_script
    assert "--source-ref refs/heads/main" in promote_script
    assert '--source-digest "$expected_build"' in promote_script
    assert "gh release verify-asset" not in promote_script
    assert "verify-assets" in promote_script
    assert "compare" in promote_script
    assert promote_script.count('--expected-tag "$TAG"') >= 2
    assert "git/ref/tags/$TAG" in promote_script
    assert 'gh api --method PATCH "repos/$GITHUB_REPOSITORY/releases/$release_id"' in promote_script
    assert "-F draft=false" in promote_script
    assert all("skip" not in str(step.get("if", "")).lower() for step in _workflow_steps())


def test_data_workflow_carries_one_numeric_release_id_through_draft_promotion() -> None:
    inventory = _workflow_step("Inventory exact release identity")
    inspect_existing = _workflow_step("Inspect and verify an existing same-tag release")
    create = _workflow_step("Create an empty draft for a new identity")
    promote = _workflow_step("Promote a freshly verified exact draft")
    inventory_script = str(inventory["run"])
    assert "releases?per_page=100&page=$page" in inventory_script
    assert 'echo "release_id=$release_id"' in inventory_script
    inspect_script = str(inspect_existing["run"])
    assert inspect_existing["env"] == {"RELEASE_ID": "${{ steps.inventory.outputs.release_id }}"}
    assert "releases/$RELEASE_ID" in inspect_script
    assert "releases/tags/$TAG" not in inspect_script
    assert ".id == $release_id" in inspect_script
    assert ".tag_name == $tag" in inspect_script
    create_script = str(create["run"])
    assert create["id"] == "create_release"
    assert "repos/$GITHUB_REPOSITORY/releases" in create_script
    assert "--method POST" in create_script
    assert 'echo "release_id=$release_id"' in create_script
    promote_script = str(promote["run"])
    assert promote["env"] == {
        "CREATED_RELEASE_ID": "${{ steps.create_release.outputs.release_id }}",
        "EXISTING_RELEASE_ID": "${{ steps.inventory.outputs.release_id }}",
    }
    assert "releases/$release_id" in promote_script
    assert "releases/tags/$TAG" not in promote_script
    assert ".id == $release_id" in promote_script
    assert ".tag_name == $tag" in promote_script
    assert ".draft == false and .immutable == true" in promote_script


def test_revised_data_tag_is_valid_but_revision_zero_is_not(tmp_path: Path) -> None:
    current = _write_metadata(tmp_path / "current.json", tag="data-2026-06-24-s4-r2")
    existing = _write_metadata(tmp_path / "existing.json", tag="data-2026-06-24-s4-r2")

    assert (
        compare_release_identity(current, existing, expected_tag="data-2026-06-24-s4-r2").state
        is ReleaseState.PUBLISHED_NOOP
    )

    invalid = _write_metadata(tmp_path / "invalid.json", tag="data-2026-06-24-s4-r0")
    with pytest.raises(IdentityComparisonError, match="tag"):
        compare_release_identity(invalid, existing, expected_tag="data-2026-06-24-s4-r2")
