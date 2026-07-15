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

#: get_experiment takes an EXPERIMENT URN; its first example must therefore BE one
#: (a score-set URN 404s here), so a schema-derived call resolves.
ExperimentUrnStr = Annotated[
    str,
    Field(
        description="A MaveDB experiment URN ('urn:mavedb:00000001-a'). Groups one "
        "or more score sets; find one via a score set's experiment_urn.",
        examples=["urn:mavedb:00000001-a"],
    ),
]

#: get_collection takes a COLLECTION URN. MaveDB exposes no collection-search
#: endpoint, so the example is a real curated collection (MaveMD).
CollectionUrnStr = Annotated[
    str,
    Field(
        description="A MaveDB collection URN "
        "('urn:mavedb:collection-<uuid>'). Obtain one from a member score set's "
        "official_collections (get_score_set at standard/full).",
        examples=["urn:mavedb:collection-603dafbf-4a3f-4d70-ab8c-aafb226fbff4"],
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

#: get_classified_variants needs a CALIBRATED score set; its example must therefore
#: be one that carries a primary calibration with classified variants (an
#: uncalibrated set yields not_found), so a schema-derived call returns rows.
CalibratedScoreSetUrnStr = Annotated[
    str,
    Field(
        description="A MaveDB score-set URN that carries a functional-classification "
        "calibration ('urn:mavedb:00000013-a-1'). Uncalibrated sets yield not_found "
        "-- confirm via get_score_set (score_calibrations present).",
        examples=["urn:mavedb:00000013-a-1"],
    ),
]

VariantLookupUrn = Annotated[
    str,
    Field(
        description="EITHER a full variant URN ('urn:mavedb:00000001-a-1#2', "
        "resolved directly) OR a score-set URN ('urn:mavedb:00000001-a-1', used "
        "WITH hgvs=). The first example is a full variant URN so a bare urn= call "
        "resolves; pass hgvs= alongside when urn= is a score-set URN.",
        examples=["urn:mavedb:00000001-a-1#2", "urn:mavedb:00000001-a-1"],
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

#: Facet filters carry concrete examples + declared value sets (G2/G3) so the agent
#: builds a valid faceted search without guessing. Applied server-side, null-inclusive.
TargetsFilter = Annotated[
    list[str] | None,
    Field(
        default=None,
        description="Filter to score sets whose target gene is one of these HGNC "
        "symbols (server-side facet).",
        examples=[["BRCA1"], ["TP53", "PTEN"]],
    ),
]

OrganismsFilter = Annotated[
    list[str] | None,
    Field(
        default=None,
        description="Filter to these target organisms (client-side, null-inclusive). "
        "Use full scientific names.",
        examples=[["Homo sapiens"], ["Saccharomyces cerevisiae"]],
    ),
]

TargetTypesFilter = Annotated[
    list[Literal["protein_coding", "regulatory", "other_noncoding"]] | None,
    Field(
        default=None,
        description="Filter to these MaveDB target categories (client-side, "
        "null-inclusive). Closed set: protein_coding | regulatory | other_noncoding "
        "(the exact values the runtime accepts; an unlisted value is invalid_input, "
        "never a silent-empty result).",
        examples=[["protein_coding"], ["regulatory", "other_noncoding"]],
    ),
]

AuthorsFilter = Annotated[
    list[str] | None,
    Field(
        default=None,
        description="Filter to score sets with these author name substrings (case-insensitive).",
        examples=[["Starita"], ["Findlay"]],
    ),
]
