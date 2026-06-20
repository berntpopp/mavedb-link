"""Static constants for mavedb-link: upstream API, citation, license, limits.

MaveDB exposes a public REST API at ``https://api.mavedb.org`` (paths under
``/api/v1``). Published records are world-readable; no credentials are required
for the read-only surface this server exposes.
"""

from __future__ import annotations

#: Default upstream base URL (the ``/api/v1`` prefix is included so service paths
#: are written relative, e.g. ``/score-sets/{urn}``).
DEFAULT_API_BASE_URL = "https://api.mavedb.org/api/v1"

#: The MaveDB web application (record permalinks: ``{WEB}/score-sets/{urn}``).
MAVEDB_WEB_URL = "https://www.mavedb.org"

#: Zenodo concept record id for the CC0 "MaveDB Bulk Download" (resolves to the
#: latest versioned dump: main.json + per-set scores/counts/annotations CSVs).
ZENODO_CONCEPT_ID = "11201736"

#: Local mirror SQLite schema version. Bump when the schema or stored shapes
#: change (invalidates older prebuilt artifacts). Lives here (not in the builder)
#: so the repository can read it without importing the ingest chain.
MIRROR_SCHEMA_VERSION = 2

#: Primary citation for the MaveDB platform. Callers must ALSO cite the specific
#: score-set URN and its primary publication (surfaced on each record).
RECOMMENDED_CITATION = (
    "Esposito D, Weile J, Shendure J, et al. MaveDB: an open-source platform to "
    "distribute and interpret data from multiplexed assays of variant effect. "
    "Genome Biology. 2019;20(1):223. doi:10.1186/s13059-019-1845-6. "
    "Cite the specific score-set URN (e.g. urn:mavedb:00000001-a-1), its license, "
    "and its primary publication alongside this platform reference."
)

#: Licensing summary. Code is AGPL-3.0; each score set carries its own data
#: license in ``license.shortName`` (CC0 1.0, CC BY 4.0, or CC BY-SA 4.0).
MAVEDB_LICENSE = (
    "MaveDB platform code is AGPL-3.0. Dataset licenses are PER score set "
    "(CC0 1.0, CC BY 4.0, or CC BY-SA 4.0); honor each record's license.shortName "
    "for attribution/reuse."
)

#: Error taxonomy surfaced by every tool (see mavedb_link.mcp.envelope).
ERROR_CODES: list[str] = [
    "invalid_input",
    "not_found",
    "ambiguous_query",
    "data_unavailable",
    "rate_limited",
    "upstream_unavailable",
    "internal_error",
]

#: Target-gene categories used by MaveDB (for arg help / discovery).
TARGET_CATEGORIES: list[str] = ["protein_coding", "regulatory", "other_noncoding"]

#: Pagination / size caps. Advertised in capabilities AND enforced by the
#: service, so the documented value always equals the enforced value.
MAX_SEARCH_LIMIT = 100
DEFAULT_SEARCH_LIMIT = 25
MAX_SCORES_LIMIT = 1000
DEFAULT_SCORES_LIMIT = 100
MAX_MAPPED_LIMIT = 500
DEFAULT_MAPPED_LIMIT = 50
MAX_GENE_LIMIT = 100
DEFAULT_GENE_LIMIT = 20
#: Degradation guard for the live /genes identity fetch behind get_gene_score_sets.
#: Bounds the worst case before falling back to mirror-derived thin identity; the
#: score-set listing is served from the mirror regardless, so this gates identity only.
GENE_IDENTITY_TIMEOUT_S = 5.0
#: Bounded FIFO size for the process-wide /genes identity memo.
GENE_IDENTITY_CACHE_MAX = 512
MAX_FIND_LIMIT = 100
DEFAULT_FIND_LIMIT = 25
#: Max score sets the live HGVS-resolution fallback probes before truncating (one
#: get_variant_score per set). The mirror serves the common case; this caps the
#: live-miss path so a popular gene cannot fan out unboundedly.
HGVS_PROBE_CAP = 10
MAX_CLASSIFIED_LIMIT = 1000
DEFAULT_CLASSIFIED_LIMIT = 100
MAX_COLLECTION_LIMIT = 500
DEFAULT_COLLECTION_LIMIT = 100

#: Functional-classification enum values MaveDB assigns to calibrated bins.
FUNCTIONAL_CLASSES: list[str] = ["abnormal", "normal", "not_specified"]

#: Tools that surface MaveDB's calibration interpretation layer (for discovery; A4).
CALIBRATION_TOOLS: list[str] = [
    "get_score_set",
    "get_variant_score",
    "get_variant_scores",
    "find_variant",
    "get_classified_variants",
    "get_score_distribution",
]

#: Upstream caps a search at 100 results/request (422 above that), so the service
#: fetches the top page (= MAX_SEARCH_LIMIT) to rank/facet/page client-side. This
#: covers every realistic gene/concept search (BRCA1, the largest, is ~62 sets).
SEARCH_FETCH_LIMIT = 100
#: One upstream read pulls the whole scores table (the largest MaveDB tables are
#: ~tens of thousands of rows). The by-hgvs single-variant scan AND the
#: distribution summary use this SAME start(0)+limit, so they share one cached CSV
#: per score set (identical cache key) -- repeat/warm lookups are then O(1), not
#: O(table). Keep these equal or the cache sharing silently breaks (see
#: tests/unit/test_perf_contract.py).
SCORES_FULL_SCAN_LIMIT = 200_000
#: Single-variant hgvs lookup scans the full scores table in one upstream read.
VARIANT_SCAN_LIMIT = SCORES_FULL_SCAN_LIMIT
#: get_score_distribution reads the whole scores table once for server-side stats.
DISTRIBUTION_FETCH_LIMIT = SCORES_FULL_SCAN_LIMIT
#: Histogram bin count for the distribution summary.
DISTRIBUTION_BINS = 10

#: MCP hosts (e.g. Claude Code) truncate a tool response at ~25,000 tokens. The
#: envelope estimates each response's size, exposes it as _meta.token_estimate,
#: and flags + steers (never silently exceeds) any response over this budget.
RESPONSE_TOKEN_BUDGET = 25_000
#: Rough chars-per-token divisor for the server-side token estimate (English/JSON).
TOKEN_ESTIMATE_CHARS_PER_TOKEN = 4

#: Score-table CSV namespaces selectable via get_variant_scores.
SCORE_NAMESPACES: list[str] = ["scores", "counts"]
