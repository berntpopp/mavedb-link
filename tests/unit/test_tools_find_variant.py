"""The find_variant surface exposes hgvs/gene through service and output schema."""

from __future__ import annotations

import inspect

from mavedb_link.mcp.schemas import FIND_VARIANT_SCHEMA
from mavedb_link.services.mavedb_service import MaveDBService


def test_service_find_variant_accepts_hgvs_and_gene() -> None:
    sig = inspect.signature(MaveDBService.find_variant)
    assert "hgvs" in sig.parameters
    assert "gene" in sig.parameters


def test_schema_declares_hgvs_resolution_fields() -> None:
    props = FIND_VARIANT_SCHEMA["properties"]
    assert "resolved_vrs" in props
    assert "hgvs_input" in props
    assert "probe_truncated" in props
