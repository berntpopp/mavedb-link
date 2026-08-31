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
