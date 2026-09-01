"""Exact numeric release-ID workflow binding."""

from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml  # type: ignore[import-untyped]

from mavedb_link.ingest.release_identity import validate_github_upload_url

ROOT = Path(__file__).resolve().parents[2]


def _step(name: str) -> dict[str, object]:
    workflow = yaml.safe_load((ROOT / ".github/workflows/data.yml").read_text(encoding="utf-8"))
    return next(step for step in workflow["jobs"]["publish"]["steps"] if step.get("name") == name)


def test_asset_lifecycle_remains_bound_to_one_numeric_release_id() -> None:
    upload = _step("Upload new sealed assets")
    assert upload["env"] == {"RELEASE_ID": "${{ steps.create_release.outputs.release_id }}"}
    upload_script = str(upload["run"])
    assert "gh release upload" not in upload_script
    upload_url_line = next(
        line for line in upload_script.splitlines() if line.strip().startswith("upload_url=")
    )
    upload_url = upload_url_line.split("=", 1)[1].strip().strip('"')
    assert urlsplit(upload_url).hostname == "uploads.github.com"
    assert upload_url.endswith("/releases/$RELEASE_ID/assets")
    assert '"$upload_url?name=$asset"' in upload_script
    assert "validate-upload-url" in upload_script

    promote_script = str(_step("Promote a freshly verified exact draft")["run"])
    assert "releases/$release_id" in promote_script
    assert "releases/tags/$TAG" not in promote_script
    assert "gh release verify" not in promote_script
    assert "pre-promotion-assets.json" in promote_script
    assert "post-promotion-assets.json" in promote_script
    assert "cmp" in promote_script


def test_existing_and_promoted_release_states_are_closed() -> None:
    workflow = (ROOT / ".github/workflows/data.yml").read_text(encoding="utf-8")
    assert workflow.count(".prerelease == false") >= 3
    assert workflow.count(".published_at == null") >= 2
    assert '(.published_at | type == "string")' in workflow


def test_promotion_rechecks_release_and_tag_immediately_before_patch() -> None:
    """A concurrent API writer cannot change the sealed release before promotion."""
    script = str(_step("Promote a freshly verified exact draft")["run"])
    assert 'prepatch_response="$RUNNER_TEMP/pre-patch-release.json"' in script
    assert 'prepatch_assets="$RUNNER_TEMP/pre-patch-assets.json"' in script
    verification = script.index("verify-assets")
    fresh_release = script.index(
        'timeout 60s gh api "repos/$GITHUB_REPOSITORY/releases/$release_id" '
        '\\\n  >"$prepatch_response"'
    )
    fresh_assets = script.index('cmp "$RUNNER_TEMP/pre-promotion-assets.json" "$prepatch_assets"')
    fresh_tag = script.index('timeout 60s gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG"')
    patch = script.index(
        'timeout 2m gh api --method PATCH "repos/$GITHUB_REPOSITORY/releases/$release_id"'
    )
    post_assets = script.index(
        'cmp "$RUNNER_TEMP/pre-promotion-assets.json" "$RUNNER_TEMP/post-promotion-assets.json"'
    )
    post_tag = script.index(
        'timeout 60s gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG"', fresh_tag + 1
    )

    assert verification < fresh_release < fresh_assets < fresh_tag < patch
    assert patch < post_assets < post_tag
    prepatch_block = " ".join(script[fresh_release:fresh_assets].split())
    assert ".id == $release_id and .tag_name == $tag" in prepatch_block
    assert ".target_commitish == $expected" in prepatch_block
    assert ".draft == true and .immutable == false and .published_at == null" in prepatch_block
    assert '.object.type == "commit" and .object.sha == $expected' in script[fresh_tag:patch]


@pytest.mark.parametrize(
    "candidate",
    [
        "https://evil.example/uploads.github.com/repos/o/r/releases/1/assets",
        "https://uploads.github.com.evil.example/repos/o/r/releases/1/assets",
        "https://uploads.github.com@evil.example/repos/o/r/releases/1/assets",
        "http://uploads.github.com/repos/o/r/releases/1/assets",
    ],
)
def test_upload_url_validation_rejects_host_boundary_spoof(candidate: str) -> None:
    with pytest.raises(ValueError, match="upload URL"):
        validate_github_upload_url(candidate)


def test_upload_url_validation_accepts_only_exact_github_upload_host() -> None:
    candidate = "https://uploads.github.com/repos/o/r/releases/1/assets?name=x"
    assert validate_github_upload_url(candidate) == candidate
