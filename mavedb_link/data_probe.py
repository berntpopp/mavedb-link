"""Read-only entry point for the fleet's MaveDB semantic data probe."""

from __future__ import annotations

import json
import sys

from mavedb_link.config import settings
from mavedb_link.runtime_data_identity import RuntimeDataIdentityError, query_data_probe


def main() -> None:
    """Print the exact closed probe result or fail without partial stdout."""
    try:
        result = query_data_probe(settings.mirror.db_path)
    except (OSError, RuntimeDataIdentityError) as exc:
        sys.stderr.write(f"MaveDB data probe failed: {exc}\n")
        raise SystemExit(1) from exc
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
