"""Shared annotated argument types for the MaveDB MCP tools."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

ResponseMode = Annotated[
    Literal["minimal", "compact", "standard", "full"],
    Field(description="Verbosity: minimal|compact|standard|full (default compact)."),
]

UrnStr = Annotated[
    str,
    Field(
        description="A MaveDB URN (e.g. score set 'urn:mavedb:00000001-a-1', "
        "experiment 'urn:mavedb:00000001-a', collection URN).",
        examples=["urn:mavedb:00000001-a-1", "urn:mavedb:00000001-a"],
    ),
]

ScoreSetUrnStr = Annotated[
    str,
    Field(
        description="A MaveDB score-set URN ('urn:mavedb:00000001-a-1'). Find one "
        "via search_score_sets or get_gene_score_sets.",
        examples=["urn:mavedb:00000001-a-1"],
    ),
]

VariantLookupUrn = Annotated[
    str,
    Field(
        description="EITHER a score-set URN ('urn:mavedb:00000001-a-1', used with "
        "hgvs=) OR a full variant URN ('urn:mavedb:00000001-a-1#2', resolved "
        "directly).",
        examples=["urn:mavedb:00000001-a-1", "urn:mavedb:00000001-a-1#2"],
    ),
]

SymbolStr = Annotated[
    str,
    Field(
        description="An HGNC gene symbol (e.g. BRCA1, TP53, PTEN).",
        examples=["BRCA1", "TP53", "PTEN"],
    ),
]

SearchText = Annotated[
    str | None,
    Field(
        default=None,
        description="Free-text query over gene/target, title, and abstract.",
        examples=["BRCA1", "deep mutational scanning", "TP53 saturation"],
    ),
]

StringList = Annotated[
    list[str] | None,
    Field(default=None, description="Optional list of filter values."),
]
