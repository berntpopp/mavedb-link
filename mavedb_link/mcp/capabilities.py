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
    RESPONSE_TOKEN_BUDGET,
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
    "response_token_budget",
    "mirror",
    "latency_profile",
    "recommended_workflows",
    "calibration_surface",
    "tool_hints",
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
            "truncated",
            "token_estimate",
            "data_source",
            "mirror_as_of",
            "capabilities_version",
            "next_commands",
        ],
        "per_call_meta_semantics": (
            "Every response's _meta carries uniform observability scalars -- tool, "
            "request_id, elapsed_ms, truncated (a machine-readable completeness "
            "signal), and token_estimate (~chars/4) -- at EVERY response_mode, so a "
            "caller always has a reliable latency + completeness signal. When a local "
            "mirror is active, _meta also reports data_source (mirror|live|mixed) and "
            "mirror_as_of (the snapshot date) for served calls. compact/standard/full "
            "additionally carry next_commands + capabilities_version; minimal is the "
            "guidance opt-out (observability scalars only). A response whose "
            "token_estimate exceeds response_token_budget also carries "
            "_meta.budget_exceeded + _meta.steer with a leaner re-call."
        ),
        "response_token_budget": RESPONSE_TOKEN_BUDGET,
        "mirror": (
            "A local SQLite mirror built from the CC0 MaveDB Zenodo bulk dump is the "
            "PRIMARY source when present; the live API is the backup. Score-set/"
            "experiment records, the scores/counts tables, full-text search, the "
            "score distribution, the cross-dataset VRS rollup, and the per-set "
            "mapped-variant enumeration (current-only compact/minimal) are served "
            "from a local index; a mirror-miss (e.g. a record newer than the snapshot) "
            "transparently falls back to the live API. _meta.data_source "
            "(mirror|live|mixed) + mirror_as_of report provenance per call; "
            "get_diagnostics.mirror reports the live snapshot status."
        ),
        "latency_profile": (
            "With a local mirror active (get_diagnostics.mirror.present=true), "
            "get_score_set/get_variant_score/get_variant_scores/get_score_distribution, "
            "get_mapped_variants (current-only compact/minimal) and search are served "
            "from a local SQLite index -- sub-ms and offline, no "
            "network scan. Without a mirror (live-only), get_variant_score (by hgvs) "
            "and get_score_distribution read a score set's full scores table in one "
            "upstream read (~1-3s cold for the largest sets), cached per set and shared "
            "across both tools so repeats are warm; a known variant URN resolves "
            "directly (one record read). Read _meta.elapsed_ms + _meta.data_source for "
            "the realised per-call latency and which backend answered."
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
            "matches outrank name/abstract namesakes. search_experiments applies the "
            "same gene-aware re-rank (experiments whose score sets target the gene "
            "rank first; reranked_by='target_gene'). get_gene_score_sets resolves an "
            "HGNC symbol to the COMPLETE dataset set (HGNC union target-name, deduped). "
            "get_variant_score returns ONE variant's score by variant URN or by "
            "score-set URN + hgvs. find_variant rolls a variant up across EVERY "
            "score set by VRS id OR variant_urn (the variant URN is resolved to its "
            "VRS internally, so no map-first step is needed)."
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
            "_meta.next_commands includes a ready-to-call forward-page step. The "
            f"{RESPONSE_TOKEN_BUDGET}-token cap is ENFORCED, not just reported: a list "
            "page over the cap is deterministically trimmed (trailing rows dropped, "
            "returned/next_offset/next_start lowered, _meta.budget_exceeded + steer "
            "set) so it stays re-pageable and never exceeds the host limit; the "
            "dropped rows remain reachable via the real total. A record (no page "
            "contract) is flagged + steered but never trimmed. Never infer "
            "completeness from list length -- read _meta.truncated, which is present "
            "on EVERY response (true when a list page or the token budget cut the result)."
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
            "{accession, variant_index, score, classification}. Discovery listings "
            "(search_score_sets, get_gene_score_sets) are intentionally lighter than "
            "the record: targets collapse to gene-name strings and the curated "
            "calibration ladder is replaced by has_calibrations (read it via "
            "get_score_set) regardless of response_mode."
        ),
        "recommended_workflows": [
            "gene -> get_gene_score_sets -> get_score_set -> get_variant_scores",
            "text -> search_score_sets -> get_score_set -> get_variant_scores",
            "score set + hgvs -> get_variant_score (score + calibrated class, no paging)",
            "variant -> get_variant_score -> find_variant(variant_urn=) "
            "(every assay that measured this variant, in one hop)",
            "score set -> get_classified_variants(classification=abnormal) (all PS3 variants)",
            "VRS allele OR variant_urn -> find_variant (same variant's score/class "
            "across ALL score sets)",
            "score set -> get_score_distribution(score=) (summary stats + a score's percentile)",
            "score set -> get_mapped_variants (VRS alleles + ClinGen Allele IDs)",
            "score set -> get_experiment (parent context) -> get_score_set (siblings)",
            "score set -> get_score_set (official_collections) -> get_collection "
            "(curated multi-dataset collection; no collection search endpoint exists)",
        ],
        "calibration_surface": {
            "note": (
                "MaveDB curates functional-classification calibrations for a MINORITY "
                "of score sets; there is NO upstream aggregate/count endpoint, so "
                "coverage is discovered per record (the field below is absent when a "
                "set carries no calibrations). get_diagnostics reports the upstream "
                "api_version and confirms the interpretation layer is supported. "
                "DISCOVERY listings (search_score_sets, get_gene_score_sets) carry only "
                "a has_calibrations:true flag, NOT the per-bin ladder — open the record "
                "with get_score_set to read the thresholds. Emitted thresholds, OddsPath "
                "ratios and baselines are rounded to 6 significant figures."
            ),
            "search_score_sets": "results[].has_calibrations (presence flag; ladder via get_score_set)",
            "get_gene_score_sets": "score_sets[].has_calibrations (presence flag; ladder via get_score_set)",
            "get_score_set": "score_calibrations (thresholds, ACMG, OddsPath, baseline)",
            "get_variant_score": "variants[].classifications + top-level calibrations",
            "get_variant_scores": (
                "rows[].classification on every page + top-level calibrations ladder "
                "on the first page (start=0) or full only (it is record-level data, "
                "not re-shipped per page)"
            ),
            "find_variant": "hits[].classifications",
            "get_classified_variants": "variants[].classification (+ acmg)",
            "get_score_distribution": "query.classifications + calibrations",
        },
        "tool_hints": {
            "get_variant_score": [
                "SGE/saturation sets often leave hgvs_pro null -- match on the c. "
                "(nucleotide) form, not p., or the lookup 404s.",
                "hgvs matching is accession-prefix-insensitive: a bare 'c.8168A>G' "
                "resolves a stored 'ENST00000380152.8:c.8168A>G' and vice-versa.",
                "The full threshold ladder is gated to response_mode='full'; compact/"
                "standard carry the per-variant matched band inline (classifications).",
            ],
            "get_variant_scores": [
                "A full ~1000-row page at standard can exceed the 25k token budget "
                "(_meta.budget_exceeded) -- use response_mode='minimal' or page via start=.",
                "Rows carry variant_index; JOIN get_mapped_variants on it, never zip.",
                "The top-level calibrations ladder ships once (the first page, start=0) "
                "or at full; forward pages carry only the per-row classification. Open "
                "get_score_set for the full ladder if you started past page 0.",
            ],
            "find_variant": [
                "Pass variant_urn to roll a variant up across every score set without "
                "mapping it first (the VRS is resolved internally).",
                "ClinGen Allele IDs are not accepted upstream -- pass the variant_urn.",
                "With a mirror active, the variant_urn->VRS resolution and the rollup "
                "are mirror-served; only enrich=true (per-hit score/class) adds live "
                "hops, so _meta.data_source reads 'mixed' there and 'mirror' when "
                "enrich=false.",
            ],
            "get_hgvs_validation": [
                "Validation is idempotent and memoised in-process, so a repeated HGVS "
                "string returns immediately without the ~1.6s upstream round-trip.",
            ],
            "get_mapped_variants": [
                "Some variants are unmapped, so this list and get_variant_scores can "
                "differ in length -- JOIN on variant_urn/variant_index, never by row.",
                "The default (current_only, compact/minimal) read is mirror-served and "
                "fast; standard/full (full VRS objects) and current_only=False reach "
                "the live endpoint (_meta.data_source reports which answered).",
            ],
            "get_score_set": [
                "score_calibrations is present only for the MINORITY of sets MaveDB "
                "has curated; it is absent (not empty) otherwise.",
            ],
            "search_score_sets": [
                "A gene-symbol query is re-ranked so target-gene matches outrank "
                "name/abstract namesakes; use facet_mode='strict' to drop "
                "unknown-metadata records.",
                "Listing rows are lean: targets collapse to gene-name strings and a "
                "calibrated set shows has_calibrations:true (not the ladder) -- open "
                "the record with get_score_set for thresholds and full target detail.",
            ],
            "get_gene_score_sets": [
                "Listing rows carry has_calibrations:true when a set is calibrated; the "
                "per-bin ACMG/OddsPath ladder is record-level -- fetch it with "
                "get_score_set(urn=) rather than expecting it inline here.",
            ],
            "get_score_distribution": [
                "Server-side summary (MaveDB has no stats endpoint); pass score= for a "
                "value's percentile + class. The full ladder is gated to 'full'.",
            ],
            "get_classified_variants": [
                "Resolves the primary calibration unless calibration_urn is given; a "
                "set with no calibration yields not_found.",
            ],
            "get_collection": [
                "MaveDB exposes NO collection search/list endpoint. Obtain a valid "
                "collection URN from a member score set's official_collections "
                "(get_score_set at standard/full; after_get_score_set also steers here "
                "when a set is a collection member), or browse mavedb.org. Example: "
                "urn:mavedb:collection-603dafbf-4a3f-4d70-ab8c-aafb226fbff4 (MaveMD).",
            ],
            "get_experiment": [
                "score_set_urns lists only the CURRENT (non-superseded) score sets, so "
                "an experiment whose -a-1 was replaced reports just -a-2 (num_score_sets "
                "counts current only). Reach a superseded version via the current set's "
                "superseded_score_set_urn (get_score_set at standard/full).",
            ],
        },
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
