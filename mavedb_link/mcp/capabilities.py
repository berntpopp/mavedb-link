"""Capabilities payload and mavedb:// discovery resources."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from mavedb_link import __version__
from mavedb_link.buildinfo import build_info
from mavedb_link.config import settings
from mavedb_link.constants import (
    DEFAULT_CLASSIFIED_LIMIT,
    DEFAULT_COLLECTION_LIMIT,
    DEFAULT_FIND_LIMIT,
    DEFAULT_GENE_LIMIT,
    DEFAULT_MAPPED_LIMIT,
    DEFAULT_SCORES_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    ERROR_CODES,
    FUNCTIONAL_CLASSES,
    MAVEDB_LICENSE,
    MAX_CLASSIFIED_LIMIT,
    MAX_COLLECTION_LIMIT,
    MAX_FIND_LIMIT,
    MAX_GENE_LIMIT,
    MAX_MAPPED_LIMIT,
    MAX_SCORES_LIMIT,
    MAX_SEARCH_LIMIT,
    RECOMMENDED_CITATION,
    TARGET_CATEGORIES,
)
from mavedb_link.mcp.arg_help import tool_signature
from mavedb_link.mcp.resources import (
    MAVEDB_REFERENCE_NOTES,
    MAVEDB_USAGE_NOTES,
    RESEARCH_USE_NOTICE,
)
from mavedb_link.services.shaping import DEFAULT_RESPONSE_MODE, RESPONSE_MODES

if TYPE_CHECKING:
    from fastmcp import FastMCP

#: Frozen tool surface. capabilities.TOOLS must equal the registered tool set
#: (enforced by tests/unit/test_tool_names.py).
TOOLS: list[str] = [
    "get_server_capabilities",
    "get_diagnostics",
    "search_score_sets",
    "get_score_set",
    "get_variant_scores",
    "get_variant_score",
    "get_gene_score_sets",
    "get_experiment",
    "search_experiments",
    "get_mapped_variants",
    "get_collection",
    "find_variant",
    "get_hgvs_validation",
    "get_classified_variants",
    "get_score_distribution",
]

_SUMMARY_KEYS: tuple[str, ...] = (
    "server",
    "server_version",
    "build",
    "capabilities_version",
    "data_source",
    "research_use_only",
    "research_use_notice",
    "recommended_citation",
    "license",
    "tools",
    "tool_count",
    "response_modes",
    "default_response_mode",
    "recommended_workflows",
    "calibration_semantics",
    "identifier_scheme",
    "search_semantics",
    "facet_honesty",
    "truncation_contract",
    "error_codes",
    "limits",
    "read_only",
)

#: capabilities_version is a content hash of the discovery CONTRACT. ``build``
#: (per-deploy sha/timestamp) and the self-hash are excluded so unrelated
#: redeploys do not churn the value -- a warm client diffs it to skip re-fetching.
_HASH_EXCLUDE: frozenset[str] = frozenset({"build", "capabilities_version"})
_VERSION_CACHE: dict[str, str] = {}


def _hash_contract(payload: dict[str, Any]) -> str:
    """Deterministic short hash of the discovery contract (volatile keys removed)."""
    contract = {k: v for k, v in payload.items() if k not in _HASH_EXCLUDE}
    blob = json.dumps(contract, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def capabilities_version() -> str:
    """Cached content hash of the discovery contract (echoed in every ``_meta``)."""
    cached = _VERSION_CACHE.get("static")
    if cached is None:
        cached = build_capabilities()["capabilities_version"]
        _VERSION_CACHE["static"] = cached
    return cached


def build_capabilities() -> dict[str, Any]:
    """Return the discovery surface describing this server."""
    payload: dict[str, Any] = {
        "server": "mavedb-link",
        "server_version": __version__,
        "build": build_info(),
        "data_source": (
            "MaveDB public REST API (api.mavedb.org) — Multiplexed Assays of "
            f"Variant Effect. Upstream base URL: {settings.api.base_url}. "
            "Only published records are returned."
        ),
        "research_use_only": True,
        "research_use_notice": RESEARCH_USE_NOTICE,
        "recommended_citation": RECOMMENDED_CITATION,
        "license": MAVEDB_LICENSE,
        "tools": TOOLS,
        "tool_count": len(TOOLS),
        "response_modes": list(RESPONSE_MODES),
        "default_response_mode": DEFAULT_RESPONSE_MODE,
        "target_categories": list(TARGET_CATEGORIES),
        "provenance_policy": (
            "Static provenance (research-use restriction, platform citation, "
            "per-record license semantics) is declared here and applies to ALL "
            "tool outputs; it is not repeated per call to conserve context tokens. "
            "Each score-set payload still carries its own license short name."
        ),
        "per_call_meta": [
            "tool",
            "request_id",
            "elapsed_ms",
            "capabilities_version",
            "next_commands",
        ],
        "per_call_meta_semantics": (
            "_meta verbosity is tiered by response_mode: minimal returns only "
            "{tool, request_id}; compact (default) adds next_commands + "
            "capabilities_version but omits elapsed_ms; standard/full add "
            "elapsed_ms. Every compact-or-richer response carries next_commands; "
            "minimal is the explicit opt-out."
        ),
        "capabilities_version_semantics": (
            "_meta.capabilities_version is a content hash of this discovery "
            "contract. A warm client caches the last value and skips re-fetching "
            "get_server_capabilities while it is unchanged."
        ),
        "identifier_scheme": (
            "MaveDB URNs: experiment set urn:mavedb:00000001, experiment "
            "urn:mavedb:00000001-a, score set urn:mavedb:00000001-a-1, variant "
            "urn:mavedb:00000001-a-1#2044. Variants carry HGVS (hgvs_nt/hgvs_pro/"
            "hgvs_splice); mapped variants are GA4GH VRS alleles with optional "
            "ClinGen Allele IDs."
        ),
        "search_semantics": (
            "search_score_sets / search_experiments are full-text + faceted search "
            "(POST). search_score_sets facets: targets (gene), target_organism_names, "
            "target_types, authors; a gene-token query is re-ranked so target-gene "
            "matches outrank name/abstract namesakes. get_gene_score_sets resolves an "
            "HGNC symbol to the COMPLETE dataset set (HGNC union target-name, deduped). "
            "get_variant_score returns ONE variant's score by variant URN or by "
            "score-set URN + hgvs."
        ),
        "facet_honesty": (
            "target_organism_names / target_types are applied client-side. The "
            "default facet_mode='inclusive' is null-inclusive: a record whose "
            "upstream metadata is empty is KEPT, never silently dropped. "
            "facet_mode='strict' drops unknown-metadata records too. Either way, "
            "search_score_sets reports _meta.facet_excluded {field: count} for every "
            "record it dropped."
        ),
        "truncation_contract": (
            "List tools return total (when known), returned, limit, offset, "
            "truncated, and next_offset. get_variant_scores additionally mirrors "
            "start/next_start (and accepts offset as an alias for start), and now "
            "carries a real total (= num_variants). When truncated, "
            "_meta.next_commands includes a ready-to-call forward-page step. Never "
            "infer completeness from list length."
        ),
        "response_mode_semantics": (
            "full returns the complete record incl. heavy free text (abstract/method "
            "text, dataset columns, score ranges) and full author lists; standard "
            "returns the structured record but elides those blobs and caps author "
            "lists to first_author + author_count; compact (default) drops "
            "null/empty values and trims to high-signal fields; minimal keeps "
            "identity anchors only (urn + title/name). minimal is uniformly lean: "
            "get_gene_score_sets minimal returns gene id + [{urn,title}] with "
            "coverage under _meta; get_variant_scores minimal drops HGVS columns to "
            "{accession, variant_index, score, classification}."
        ),
        "recommended_workflows": [
            "gene -> get_gene_score_sets -> get_score_set -> get_variant_scores",
            "text -> search_score_sets -> get_score_set -> get_variant_scores",
            "score set + hgvs -> get_variant_score (score + calibrated class, no paging)",
            "score set -> get_classified_variants(classification=abnormal) (all PS3 variants)",
            "VRS allele -> find_variant (same variant's score/class across ALL score sets)",
            "score set -> get_score_distribution(score=) (summary stats + a score's percentile)",
            "score set -> get_mapped_variants (VRS alleles + ClinGen Allele IDs)",
            "score set -> get_experiment (parent context) -> get_score_set (siblings)",
        ],
        "calibration_semantics": (
            "get_score_set surfaces score_calibrations (MaveDB's curated "
            "functional-classification thresholds); get_variant_score, every "
            "get_variant_scores row, find_variant hits, and get_score_distribution "
            f"queries carry the derived class ({' | '.join(FUNCTIONAL_CLASSES)} | "
            "indeterminate). Classification is range-driven and direction-agnostic "
            "(a set's scale may run either way); a score in an inter-bin gap is "
            "'indeterminate', never snapped to the nearest class. A set may carry "
            "0, 1, or several calibrations, each yielding its own classification "
            "with an ACMG criterion (PS3/BS3) + evidence strength and OddsPath ratio."
        ),
        "citation_contract": (
            "Cite the score-set URN, its per-record license (license field), and "
            "its primary publication, alongside the MaveDB platform reference in "
            "recommended_citation."
        ),
        "error_codes": ERROR_CODES,
        "limits": {
            "max_search_limit": MAX_SEARCH_LIMIT,
            "default_search_limit": DEFAULT_SEARCH_LIMIT,
            "max_scores_limit": MAX_SCORES_LIMIT,
            "default_scores_limit": DEFAULT_SCORES_LIMIT,
            "max_mapped_limit": MAX_MAPPED_LIMIT,
            "default_mapped_limit": DEFAULT_MAPPED_LIMIT,
            "max_gene_limit": MAX_GENE_LIMIT,
            "default_gene_limit": DEFAULT_GENE_LIMIT,
            "max_find_limit": MAX_FIND_LIMIT,
            "default_find_limit": DEFAULT_FIND_LIMIT,
            "max_classified_limit": MAX_CLASSIFIED_LIMIT,
            "default_classified_limit": DEFAULT_CLASSIFIED_LIMIT,
            "max_collection_limit": MAX_COLLECTION_LIMIT,
            "default_collection_limit": DEFAULT_COLLECTION_LIMIT,
        },
        "read_only": True,
        "notes": MAVEDB_REFERENCE_NOTES,
    }
    payload["capabilities_version"] = _hash_contract(payload)
    return payload


async def collect_tool_signatures(mcp: FastMCP) -> dict[str, str]:
    """Map every registered tool to its rendered signature (from the live schema)."""
    tools = sorted(await mcp.list_tools(), key=lambda t: t.name)
    return {t.name: tool_signature(t.name, t.parameters or {}) for t in tools}


async def build_tools_overview(mcp: FastMCP) -> dict[str, Any]:
    """Lightweight discovery payload: name, one-line summary, and call signature."""
    tools = sorted(await mcp.list_tools(), key=lambda t: t.name)
    entries: list[dict[str, str]] = []
    for tool in tools:
        summary = (tool.description or "").split(". ")[0].strip()
        entries.append(
            {
                "name": tool.name,
                "summary": summary[:200],
                "signature": tool_signature(tool.name, tool.parameters or {}),
            }
        )
    return {"server": "mavedb-link", "tool_count": len(entries), "tools": entries}


def project_capabilities(
    detail: str, tool_signatures: dict[str, str] | None = None
) -> dict[str, Any]:
    """Return the full capabilities payload, or a light summary (default)."""
    full = build_capabilities()
    if tool_signatures is not None:
        full["tool_signatures"] = tool_signatures
    if detail == "full":
        full["detail"] = "full"
        return full
    summary: dict[str, Any] = {k: full[k] for k in _SUMMARY_KEYS if k in full}
    if tool_signatures is not None:
        summary["tool_signatures"] = tool_signatures
    summary["detail"] = "summary"
    summary["more"] = (
        "Call get_server_capabilities(detail='full') or read mavedb://capabilities "
        "for workflows, semantics, and reference notes; mavedb://tools lists "
        "call signatures."
    )
    return summary


def register_capability_resources(mcp: FastMCP) -> None:
    """Register the mavedb:// resource family on a FastMCP instance."""

    @mcp.resource("mavedb://capabilities", mime_type="application/json")
    def capabilities() -> str:
        return json.dumps(build_capabilities(), indent=2)

    @mcp.resource("mavedb://tools", mime_type="application/json")
    async def tools_overview() -> str:
        return json.dumps(await build_tools_overview(mcp), indent=2)

    @mcp.resource("mavedb://usage", mime_type="text/plain")
    def usage() -> str:
        return MAVEDB_USAGE_NOTES

    @mcp.resource("mavedb://reference", mime_type="text/plain")
    def reference() -> str:
        return MAVEDB_REFERENCE_NOTES

    @mcp.resource("mavedb://research-use", mime_type="text/plain")
    def research_use() -> str:
        return RESEARCH_USE_NOTICE

    @mcp.resource("mavedb://citation", mime_type="text/plain")
    def citation() -> str:
        return RECOMMENDED_CITATION
