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
MAX_FIND_LIMIT = 100
DEFAULT_FIND_LIMIT = 25
MAX_CLASSIFIED_LIMIT = 1000
DEFAULT_CLASSIFIED_LIMIT = 100
MAX_COLLECTION_LIMIT = 500
DEFAULT_COLLECTION_LIMIT = 100

#: Functional-classification enum values MaveDB assigns to calibrated bins.
FUNCTIONAL_CLASSES: list[str] = ["abnormal", "normal", "not_specified"]

#: Upstream caps a search at 100 results/request (422 above that), so the service
#: fetches the top page (= MAX_SEARCH_LIMIT) to rank/facet/page client-side. This
#: covers every realistic gene/concept search (BRCA1, the largest, is ~62 sets).
SEARCH_FETCH_LIMIT = 100
#: Single-variant hgvs lookup scans the full scores table in one upstream read
#: (the largest MaveDB tables are ~tens of thousands of rows); cached thereafter.
VARIANT_SCAN_LIMIT = 200_000
#: get_score_distribution reads the whole scores table once to compute summary
#: statistics server-side (a summary instead of returning every row).
DISTRIBUTION_FETCH_LIMIT = 200_000
#: Histogram bin count for the distribution summary.
DISTRIBUTION_BINS = 10

#: Score-table CSV namespaces selectable via get_variant_scores.
SCORE_NAMESPACES: list[str] = ["scores", "counts"]
