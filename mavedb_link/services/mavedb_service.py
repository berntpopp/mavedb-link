"""MaveDBService: async domain methods over the MaveDB API (data plane).

Each method returns a plain dict (shaped by ``response_mode``) and raises typed
exceptions; it never builds an MCP envelope. List methods attach a uniform
pagination block ``{total, returned, limit, offset, truncated, next_offset}``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mavedb_link.api.client import MaveDBClient
from mavedb_link.constants import (
    CALIBRATION_TOOLS,
    DEFAULT_CLASSIFIED_LIMIT,
    DEFAULT_COLLECTION_LIMIT,
    DEFAULT_FIND_LIMIT,
    DEFAULT_GENE_LIMIT,
    DEFAULT_MAPPED_LIMIT,
    DEFAULT_SCORES_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    FUNCTIONAL_CLASSES,
    MAX_COLLECTION_LIMIT,
    MAX_GENE_LIMIT,
    MAX_MAPPED_LIMIT,
    MAX_SCORES_LIMIT,
    MAX_SEARCH_LIMIT,
    SEARCH_FETCH_LIMIT,
)
from mavedb_link.data.hybrid import mirror_status
from mavedb_link.exceptions import InvalidInputError
from mavedb_link.identifiers import (
    looks_like_gene_symbol,
    validate_score_set_urn,
    variant_index_of,
)
from mavedb_link.services import distribution, resolvers, shaping, variant_lookup
from mavedb_link.services.calibration import (
    INDETERMINATE,
    primary_classification,
    shape_calibrations,
)
from mavedb_link.services.scores import shape_scores
from mavedb_link.services.search import (
    apply_sparse_facets,
    rank_by_target_match,
    rank_experiments_by_target,
)


def _clamp(value: int, lo: int, hi: int) -> int:
    """Clamp ``value`` into ``[lo, hi]``."""
    return max(lo, min(value, hi))


def _extract_items(
    resp: Any, item_keys: tuple[str, ...], total_keys: tuple[str, ...]
) -> tuple[list[Any], int | None]:
    """Pull ``(items, total)`` from a search response (list or wrapper dict)."""
    if isinstance(resp, list):
        return resp, len(resp)
    if isinstance(resp, dict):
        for key in item_keys:
            if isinstance(resp.get(key), list):
                items = resp[key]
                total = next((resp[t] for t in total_keys if isinstance(resp.get(t), int)), None)
                return items, total
    return [], 0


def _mapped_variant_urn(item: Any) -> str:
    """A mapped-variant record's source variant URN (or empty)."""
    if not isinstance(item, dict):
        return ""
    return item.get("variantUrn") or (item.get("variant") or {}).get("urn") or ""


def _mapped_sort_key(item: Any) -> tuple[int, str]:
    """Numeric sort key (``#index``, urn) so rows order #1,#2,…,#10 — not #1,#10,#2.

    Lexical sort of the variant URN string mispairs rows when zipped against the
    numerically-ordered scores table (F1). Variants with no parseable index sort
    last (deterministically, by URN string).
    """
    urn = _mapped_variant_urn(item)
    index = variant_index_of(urn)
    return (index if index is not None else 2**62, urn)


def _page_block(*, total: int | None, returned: int, limit: int, offset: int) -> dict[str, Any]:
    """Build the uniform pagination block for a list payload."""
    truncated = offset + returned < total if total is not None else returned >= limit
    return {
        "total": total,
        "returned": returned,
        "limit": limit,
        "offset": offset,
        "truncated": truncated,
        "next_offset": offset + returned if truncated else None,
    }


class MaveDBService:
    """Read-only domain service over the MaveDB REST API."""

    def __init__(self, client: MaveDBClient) -> None:
        """Wrap an :class:`MaveDBClient` (one shared instance per process)."""
        self._client = client

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def search_score_sets(
        self,
        text: str | None = None,
        *,
        published: bool = True,
        targets: list[str] | None = None,
        target_organism_names: list[str] | None = None,
        target_types: list[str] | None = None,
        authors: list[str] | None = None,
        facet_mode: str = "inclusive",
        limit: int = DEFAULT_SEARCH_LIMIT,
        offset: int = 0,
        response_mode: str = shaping.DEFAULT_RESPONSE_MODE,
    ) -> dict[str, Any]:
        """Search score sets by free text and facets (``POST /score-sets/search``).

        The endpoint returns the FULL match list and ignores page params, so the
        service fetches it whole and (1) re-ranks gene-token queries by target
        match (DEF-2), (2) applies organism/target-type facets client-side
        (``facet_mode`` ``inclusive`` keeps unknown-metadata records, ``strict``
        drops them — F9), surfacing an honest ``_meta.facet_excluded`` (DEF-3),
        then (3) pages the processed list. ``targets``/``authors`` stay server-side.
        """
        if facet_mode not in ("inclusive", "strict"):
            raise InvalidInputError(
                f"Unknown facet_mode '{facet_mode}'.",
                field="facet_mode",
                allowed=["inclusive", "strict"],
            )
        capped = _clamp(limit, 1, MAX_SEARCH_LIMIT)
        body: dict[str, Any] = {"published": published, "limit": SEARCH_FETCH_LIMIT}
        for key, value in (("text", text), ("targets", targets), ("authors", authors)):
            if value:
                body[key] = value
        resp = await self._client.post_json("/score-sets/search", json=body)
        items, _ = _extract_items(
            resp, ("scoreSets", "items", "results"), ("numScoreSets", "total", "count")
        )
        kept, facet_excluded = apply_sparse_facets(
            items, target_organism_names, target_types, strict=facet_mode == "strict"
        )
        ranked = rank_by_target_match(kept, text)
        total = len(ranked)
        page = ranked[offset : offset + capped]
        results = [shaping.shape_score_set(it, response_mode) for it in page]
        payload: dict[str, Any] = {
            "query": text,
            "facet_mode": facet_mode,
            "results": results,
            **_page_block(total=total, returned=len(results), limit=capped, offset=offset),
        }
        if facet_excluded:
            payload.setdefault("_meta", {})["facet_excluded"] = facet_excluded
        return payload

    async def get_score_set(
        self, urn: str, *, response_mode: str = shaping.DEFAULT_RESPONSE_MODE
    ) -> dict[str, Any]:
        """Fetch one score-set record (``GET /score-sets/{urn}``).

        Pre-validates URN granularity (DEF-4): an experiment/collection URN yields
        ``invalid_input`` with ``field=urn`` instead of a misleading upstream 404.
        """
        score_set_urn = validate_score_set_urn(urn)
        raw = await self._client.get_json(f"/score-sets/{score_set_urn}")
        return shaping.shape_score_set(raw, response_mode)

    async def get_variant_scores(
        self,
        urn: str,
        *,
        start: int = 0,
        limit: int = DEFAULT_SCORES_LIMIT,
        drop_na_columns: bool = False,
        response_mode: str = shaping.DEFAULT_RESPONSE_MODE,
    ) -> dict[str, Any]:
        """Fetch the quantitative per-variant score table (``GET .../scores``, CSV).

        Concurrently reads the score-set record for its ``numVariants`` so the page
        carries a real ``total`` (DEF-7); the record read is best-effort and cached.
        ``offset``/``next_offset`` mirror ``start``/``next_start`` so paging params
        match the other list tools.
        """
        score_set_urn = validate_score_set_urn(urn)
        capped = _clamp(limit, 1, MAX_SCORES_LIMIT)
        params: dict[str, Any] = {"start": start, "limit": capped}
        if drop_na_columns:
            params["drop_na_columns"] = True
        gathered: Any = await asyncio.gather(
            self._client.get_text(f"/score-sets/{score_set_urn}/scores", params=params),
            self._client.get_json(f"/score-sets/{score_set_urn}"),
            return_exceptions=True,
        )
        text, record = gathered[0], gathered[1]
        if isinstance(text, BaseException):
            raise text
        num_variants: int | None = None
        raw_calibrations: list[dict[str, Any]] = []
        if isinstance(record, dict):
            if isinstance(record.get("numVariants"), int):
                num_variants = record["numVariants"]
            cals = record.get("scoreCalibrations")
            if isinstance(cals, list):
                raw_calibrations = cals
        payload = shape_scores(
            text, start=start, limit=capped, num_variants=num_variants, response_mode=response_mode
        )
        payload["urn"] = score_set_urn
        payload["offset"] = payload["start"]
        payload["next_offset"] = payload["next_start"]
        if raw_calibrations:
            for row in payload["rows"]:
                verdict = primary_classification(row.get("score"), raw_calibrations)
                if verdict:
                    row["classification"] = verdict
            payload["calibrations"] = shape_calibrations(
                raw_calibrations, full=response_mode in ("standard", "full")
            )
        return payload

    async def get_variant_score(
        self,
        urn: str,
        *,
        hgvs: str | None = None,
        response_mode: str = shaping.DEFAULT_RESPONSE_MODE,
    ) -> dict[str, Any]:
        """Look up ONE variant's score by variant URN or score-set URN + hgvs (delegated)."""
        return await variant_lookup.get_variant_score(
            self._client, urn, hgvs=hgvs, response_mode=response_mode
        )

    async def get_gene_score_sets(
        self,
        symbol: str,
        *,
        limit: int = DEFAULT_GENE_LIMIT,
        offset: int = 0,
        response_mode: str = shaping.DEFAULT_RESPONSE_MODE,
    ) -> dict[str, Any]:
        """Resolve a gene to the COMPLETE set of its published score sets (DEF-1).

        The ``/genes/{symbol}`` endpoint (HGNC resolution) and the ``targets``
        facet each return a *partial, divergent* view, so this unions both —
        deduped by URN — to honour the tool's "all MAVE data for a gene" promise.
        The two upstream reads run concurrently; the target-facet read is
        best-effort (its failure degrades to gene-only, never raises).
        """
        capped = _clamp(limit, 1, MAX_GENE_LIMIT)
        sym = symbol.strip()
        gathered: Any = await asyncio.gather(
            self._client.get_json(f"/genes/{sym}", params={"limit": MAX_GENE_LIMIT, "offset": 0}),
            self._client.post_json(
                "/score-sets/search",
                json={"published": True, "targets": [sym], "limit": MAX_SEARCH_LIMIT},
            ),
            return_exceptions=True,
        )
        gene_raw, target_resp = gathered[0], gathered[1]
        if isinstance(gene_raw, BaseException):
            raise gene_raw  # gene identity is required; propagate as the lookup error
        gene_items, _ = _extract_items(
            gene_raw, ("scoreSets", "score_sets"), ("total", "numScoreSets")
        )
        degraded = isinstance(target_resp, BaseException)
        target_items: list[Any] = []
        if not degraded:
            target_items, _ = _extract_items(
                target_resp, ("scoreSets", "items", "results"), ("numScoreSets", "total", "count")
            )
        merged: dict[str, Any] = {}
        for item in (*gene_items, *target_items):  # gene first: it wins on dedupe
            urn = item.get("urn") if isinstance(item, dict) else None
            if urn:
                merged.setdefault(urn, item)
        ordered = sorted(merged.values(), key=lambda it: it.get("urn") or "")
        total = len(ordered)
        page = ordered[offset : offset + capped]
        results = [shaping.shape_score_set(it, response_mode) for it in page]
        coverage: dict[str, Any] = {
            "sources": ["gene_endpoint", "target_search"],
            "gene_endpoint": len(gene_items),
            "target_search": len(target_items),
            "union": total,
        }
        if degraded:
            coverage["degraded"] = True
        payload: dict[str, Any] = {
            "gene": shaping.shape_gene(gene_raw, response_mode),
            "score_sets": results,
            **_page_block(total=total, returned=len(results), limit=capped, offset=offset),
        }
        if response_mode == "minimal":
            # F8: keep minimal on the identity hot path -- coverage diagnostics and
            # the scored-variant tally move off the payload into _meta.
            meta = payload.setdefault("_meta", {})
            meta["coverage"] = coverage
            meta["total_scored_variants"] = gene_raw.get("totalScoredVariants")
        else:
            payload["total_scored_variants"] = gene_raw.get("totalScoredVariants")
            payload["coverage"] = coverage
        return payload

    async def get_experiment(
        self, urn: str, *, response_mode: str = shaping.DEFAULT_RESPONSE_MODE
    ) -> dict[str, Any]:
        """Fetch one experiment record (``GET /experiments/{urn}``)."""
        raw = await self._client.get_json(f"/experiments/{urn.strip()}")
        return shaping.shape_experiment(raw, response_mode)

    async def search_experiments(
        self,
        text: str | None = None,
        *,
        published: bool = True,
        targets: list[str] | None = None,
        target_organism_names: list[str] | None = None,
        target_types: list[str] | None = None,
        authors: list[str] | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
        offset: int = 0,
        response_mode: str = shaping.DEFAULT_RESPONSE_MODE,
    ) -> dict[str, Any]:
        """Search experiments by free text (``POST /experiments/search``).

        The upstream endpoint takes no limit/offset and returns the FULL match
        list, so paging is applied client-side (total = full-list length). Target
        facets (DEF-8) are *derived* from the score-set target search — the upstream
        experiment ``targets`` facet is non-discriminating — grouped by parent
        experiment URN.
        """
        capped = _clamp(limit, 1, MAX_SEARCH_LIMIT)
        if targets or target_organism_names or target_types:
            return await self._search_experiments_by_target(
                text=text,
                targets=targets,
                target_organism_names=target_organism_names,
                target_types=target_types,
                capped=capped,
                offset=offset,
            )
        body: dict[str, Any] = {"published": published}
        for key, value in (("text", text), ("authors", authors)):
            if value:
                body[key] = value
        resp = await self._client.post_json("/experiments/search", json=body)
        items, _ = _extract_items(
            resp, ("experiments", "items", "results"), ("numExperiments", "total", "count")
        )
        reranked = False
        if text and looks_like_gene_symbol(text.strip()):
            target_urns = await self._target_experiment_urns(text.strip())
            if target_urns:
                items = rank_experiments_by_target(items, target_urns)
                reranked = True
        total = len(items)
        page = items[offset : offset + capped]
        results = [shaping.shape_experiment(it, response_mode) for it in page]
        payload: dict[str, Any] = {
            "query": text,
            "results": results,
            **_page_block(total=total, returned=len(results), limit=capped, offset=offset),
        }
        if reranked:
            payload["reranked_by"] = "target_gene"
        return payload

    async def _target_experiment_urns(self, symbol: str) -> set[str]:
        """Experiment URNs whose score sets target ``symbol`` (best-effort; A2).

        The experiment search endpoint has no target facet, so target-relevance is
        derived from the score-set target search and projected to parent experiment
        URNs. An upstream failure degrades to "no boost", never raises.
        """
        try:
            resp = await self._client.post_json(
                "/score-sets/search",
                json={"published": True, "targets": [symbol], "limit": MAX_SEARCH_LIMIT},
            )
        except Exception:  # best-effort: the re-rank is an enhancement, not required
            return set()
        items, _ = _extract_items(
            resp, ("scoreSets", "items", "results"), ("numScoreSets", "total", "count")
        )
        urns: set[str] = set()
        for score_set in items:
            if not isinstance(score_set, dict):
                continue
            exp = (score_set.get("experiment") or {}).get("urn") or score_set.get("experimentUrn")
            if exp:
                urns.add(exp)
        return urns

    async def _search_experiments_by_target(
        self,
        *,
        text: str | None,
        targets: list[str] | None,
        target_organism_names: list[str] | None,
        target_types: list[str] | None,
        capped: int,
        offset: int,
    ) -> dict[str, Any]:
        """Derive a target-faceted experiment list by grouping score-set hits."""
        ss = await self.search_score_sets(
            text,
            targets=targets,
            target_organism_names=target_organism_names,
            target_types=target_types,
            limit=MAX_SEARCH_LIMIT,
            offset=0,
            response_mode="compact",
        )
        groups: dict[str, list[str]] = {}
        order: list[str] = []
        for score_set in ss.get("results", []):
            experiment_urn = score_set.get("experiment_urn")
            if not experiment_urn:
                continue
            if experiment_urn not in groups:
                groups[experiment_urn] = []
                order.append(experiment_urn)
            if score_set.get("urn"):
                groups[experiment_urn].append(score_set["urn"])
        entries = [
            {
                "urn": experiment_urn,
                "score_set_urns": groups[experiment_urn],
                "num_matching_score_sets": len(groups[experiment_urn]),
                "source": "derived_from_target_search",
            }
            for experiment_urn in order
        ]
        total = len(entries)
        page = entries[offset : offset + capped]
        return {
            "query": text,
            "results": page,
            "derived_from": "score_set_target_search",
            **_page_block(total=total, returned=len(page), limit=capped, offset=offset),
        }

    async def get_mapped_variants(
        self,
        urn: str,
        *,
        current_only: bool = True,
        limit: int = DEFAULT_MAPPED_LIMIT,
        offset: int = 0,
        response_mode: str = shaping.DEFAULT_RESPONSE_MODE,
    ) -> dict[str, Any]:
        """Fetch genome-mapped (VRS) alleles for a score set (``GET …/mapped-variants``).

        Upstream emits both the current and superseded mapping per variant (a 2x
        doubling); ``current_only`` (default) collapses to one row per variant.
        Upstream returns the list UNORDERED, so rows are sorted **numerically by
        the variant index** (``#1, #2, … #10``) to match ``get_variant_scores``;
        each row carries ``variant_index``. Because some variants may be unmapped,
        the two lists can differ in length — **join on ``variant_urn`` /
        ``variant_index``, do not zip by row position** (F1).
        """
        score_set_urn = validate_score_set_urn(urn)
        capped = _clamp(limit, 1, MAX_MAPPED_LIMIT)
        raw = await self._client.get_json(f"/score-sets/{score_set_urn}/mapped-variants")
        items = raw if isinstance(raw, list) else (raw.get("mappedVariants") or [])
        if current_only:
            items = [it for it in items if isinstance(it, dict) and it.get("current")]
        items = sorted(items, key=_mapped_sort_key)
        total = len(items)
        page = items[offset : offset + capped]
        results = [shaping.shape_mapped_variant(it, response_mode) for it in page]
        return {
            "urn": score_set_urn,
            "mapped_variants": results,
            "current_only": current_only,
            "ordering": "variant_index",
            "join_key": "variant_urn",
            **_page_block(total=total, returned=len(results), limit=capped, offset=offset),
        }

    async def find_variant(
        self,
        vrs_id: str | None = None,
        *,
        variant_urn: str | None = None,
        only_current: bool = True,
        enrich: bool = True,
        limit: int = DEFAULT_FIND_LIMIT,
        offset: int = 0,
        response_mode: str = shaping.DEFAULT_RESPONSE_MODE,
    ) -> dict[str, Any]:
        """Find a variant across every score set by VRS id OR variant URN (delegated)."""
        return await resolvers.find_variant(
            self._client,
            vrs_id,
            variant_urn=variant_urn,
            only_current=only_current,
            enrich=enrich,
            limit=limit,
            offset=offset,
            response_mode=response_mode,
        )

    async def get_hgvs_validation(self, variant: str) -> dict[str, Any]:
        """Validate an HGVS string upstream (delegated to resolvers)."""
        return await resolvers.get_hgvs_validation(self._client, variant)

    async def get_score_distribution(
        self,
        urn: str,
        *,
        score: float | None = None,
        response_mode: str = shaping.DEFAULT_RESPONSE_MODE,
    ) -> dict[str, Any]:
        """Summarise a score set's score distribution (delegated to distribution)."""
        return await distribution.score_distribution(
            self._client, urn, score=score, response_mode=response_mode
        )

    async def get_classified_variants(
        self,
        urn: str,
        *,
        classification: str | None = None,
        calibration_urn: str | None = None,
        limit: int = DEFAULT_CLASSIFIED_LIMIT,
        offset: int = 0,
        response_mode: str = shaping.DEFAULT_RESPONSE_MODE,
    ) -> dict[str, Any]:
        """Return a score set's variants in a functional class (delegated)."""
        return await resolvers.get_classified_variants(
            self._client,
            urn,
            classification=classification,
            calibration_urn=calibration_urn,
            limit=limit,
            offset=offset,
        )

    async def get_collection(
        self,
        urn: str,
        *,
        limit: int = DEFAULT_COLLECTION_LIMIT,
        offset: int = 0,
        response_mode: str = shaping.DEFAULT_RESPONSE_MODE,
    ) -> dict[str, Any]:
        """Fetch a curated collection (``GET /collections/{urn}``), paging members (F12)."""
        capped = _clamp(limit, 1, MAX_COLLECTION_LIMIT)
        raw = await self._client.get_json(f"/collections/{urn.strip()}")
        return shaping.shape_collection(raw, response_mode, limit=capped, offset=offset)

    async def get_diagnostics(self) -> dict[str, Any]:
        """Report upstream reachability + version + interpretation surface (A4).

        Never raises on upstream-down: an unreachable API is reported, not thrown.
        """
        diag: dict[str, Any] = {"base_url": self._client.base_url}
        # Mirror status first, so it is reported even when the live API is down
        # (the mirror serves offline).
        diag["mirror"] = mirror_status(self._client)
        try:
            version = await self._client.get_version()
        except Exception as exc:  # diagnostics REPORTS upstream failure, never raises
            diag["api_reachable"] = False
            diag["error"] = str(exc)[:200]
            return diag
        if isinstance(version, dict):
            diag["api_name"] = version.get("name")
            diag["api_version"] = version.get("version")
        diag["api_reachable"] = True
        diag["interpretation"] = {
            "calibration_supported": True,
            "surfaced_by": list(CALIBRATION_TOOLS),
            "functional_classes": [*FUNCTIONAL_CLASSES, INDETERMINATE],
            "note": (
                "Functional-classification calibrations (ACMG PS3/BS3, OddsPath, "
                "thresholds) are curated per score set and exist for a MINORITY of "
                "them. MaveDB exposes NO aggregate/coverage endpoint, so a population "
                "coverage count cannot be reported cheaply; discover per record via "
                "get_score_set. Classification is range-driven and direction-agnostic."
            ),
        }
        return diag
