"""Production data pins used by the centralized container smoke gate."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_smoke_preparation_pins_same_immutable_bundle_for_app_and_init(
    tmp_path: Path,
) -> None:
    config = json.loads((ROOT / "container-release.json").read_text(encoding="utf-8"))
    smoke_environment = {
        "MAVEDB_LINK_ENVIRONMENT": "production",
        "MAVEDB_LINK_MIRROR__BUNDLE_URL": (
            "https://github.com/berntpopp/mavedb-link/releases/download/"
            "data-2026-06-24-s4-r3/mavedb.sqlite.zst"
        ),
        "MAVEDB_LINK_MIRROR__BUNDLE_RELEASE_TAG": "data-2026-06-24-s4-r3",
        "MAVEDB_LINK_MIRROR__BUNDLE_EXPECTED_SHA256": (
            "b6afdf81eacae732f372c27ffab2a0bc2e0337f801912d5eaf58640fd02d3b33"
        ),
        "MAVEDB_LINK_MIRROR__BUNDLE_EXPECTED_EXPANDED_SHA256": (
            "5f2315d8036eca4c93574238f261e22d5b5199f45c370f58a0455616ad7d26b9"
        ),
        "MAVEDB_LINK_MIRROR__BUNDLE_EXPECTED_SCHEMA_VERSION": "4.0.0",
        "MAVEDB_LINK_MIRROR__DEVELOPMENT_LATEST": "false",
    }
    assert config["preparation"] == "docker/ci-prepare-smoke.sh"
    assert dict(item.split("=", 1) for item in config["smoke_environment"]) == smoke_environment

    output = tmp_path / "smoke.env"
    output.touch()
    env = {"PATH": os.environ["PATH"], "GF_SMOKE_ENV_FILE": str(output)}
    bash = shutil.which("bash")
    assert bash is not None
    subprocess.run(  # noqa: S603 - fixed repo script and arguments
        [bash, "docker/ci-prepare-smoke.sh"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert dict(line.split("=", 1) for line in output.read_text().splitlines()) == smoke_environment
