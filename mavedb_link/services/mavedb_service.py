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
    DEFAULT_CLASSIFIED_LIMIT,
    DEFAULT_FIND_LIMIT,
    DEFAULT_GENE_LIMIT,
    DEFAULT_MAPPED_LIMIT,
    DEFAULT_SCORES_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    MAX_GENE_LIMIT,
    MAX_MAPPED_LIMIT,
    MAX_SCORES_LIMIT,
    MAX_SEARCH_LIMIT,
    SEARCH_FETCH_LIMIT,
    VARIANT_SCAN_LIMIT,
)
from mavedb_link.exceptions import InvalidInputError, NotFoundError
from mavedb_link.identifiers import is_variant_urn, validate_score_set_urn
from mavedb_link.services import resolvers, shaping
from mavedb_link.services.calibration import (
    classify_score,
    primary_classification,
    shape_calibrations,
)
from mavedb_link.services.scores import hgvs_matches, parse_scores_csv, shape_scores
from mavedb_link.services.search import apply_sparse_facets, rank_by_target_match


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
    """Sort key: a mapped-variant record's source variant URN (or empty)."""
    if not isinstance(item, dict):
        return ""
    return item.get("variantUrn") or (item.get("variant") or {}).get("urn") or ""


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
        limit: int = DEFAULT_SEARCH_LIMIT,
        offset: int = 0,
        response_mode: str = shaping.DEFAULT_RESPONSE_MODE,
    ) -> dict[str, Any]:
        """Search score sets by free text and facets (``POST /score-sets/search``).

        The endpoint returns the FULL match list and ignores page params, so the
        service fetches it whole and (1) re-ranks gene-token queries by target
        match (DEF-2), (2) applies organism/target-type facets client-side,
        null-inclusively, surfacing an honest ``_meta.facet_excluded`` (DEF-3),
        then (3) pages the processed list. ``targets``/``authors`` stay server-side.
        """
        capped = _clamp(limit, 1, MAX_SEARCH_LIMIT)
        body: dict[str, Any] = {"published": published, "limit": SEARCH_FETCH_LIMIT}
        for key, value in (("text", text), ("targets", targets), ("authors", authors)):
            if value:
                body[key] = value
        resp = await self._client.post_json("/score-sets/search", json=body)
        items, _ = _extract_items(
            resp, ("scoreSets", "items", "results"), ("numScoreSets", "total", "count")
        )
        kept, facet_excluded = apply_sparse_facets(items, target_organism_names, target_types)
        ranked = rank_by_target_match(kept, text)
        total = len(ranked)
        page = ranked[offset : offset + capped]
        results = [shaping.shape_score_set(it, response_mode) for it in page]
        payload: dict[str, Any] = {
            "query": text,
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
        payload = shape_scores(text, start=start, limit=capped, num_variants=num_variants)
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
        """Look up ONE variant's score without paging the whole table (DEF-6).

        Two entry forms: a full variant URN (``…-a-1#<index>``) resolves directly
        via ``GET /variants/{urn}``; a score-set URN plus ``hgvs`` scans the score
        table once (cached thereafter) and returns the matching row(s).
        """
        candidate = urn.strip()
        if is_variant_urn(candidate):
            # The variant URN's '#<index>' must be percent-encoded or httpx drops it
            # as a URL fragment.
            raw = await self._client.get_json(f"/variants/{candidate.replace('#', '%23')}")
            payload = shaping.shape_single_variant(raw, response_mode)
            calibrations = await self._raw_calibrations(payload.get("score_set_urn"))
            classified = classify_score(payload.get("score"), calibrations)
            if classified:
                payload["classifications"] = classified
            return payload
        score_set_urn = validate_score_set_urn(candidate)
        if not hgvs or not hgvs.strip():
            raise InvalidInputError(
                "Provide hgvs= (e.g. 'c.8168A>G' or 'p.Arg1699Trp') to look up one "
                "variant, or pass a full variant URN ('urn:mavedb:...-a-1#<index>').",
                field="hgvs",
                hint="Variant URNs are the 'accession' column of get_variant_scores "
                "and 'variant_urn' in get_mapped_variants.",
            )
        text = await self._client.get_text(
            f"/score-sets/{score_set_urn}/scores", params={"start": 0, "limit": VARIANT_SCAN_LIMIT}
        )
        _columns, rows = parse_scores_csv(text)
        query = hgvs.strip().lower()
        matches = [r for r in rows if hgvs_matches(r, query)]
        if not matches:
            raise NotFoundError(
                f"No variant matching hgvs '{hgvs.strip()}' in {score_set_urn} "
                f"(scanned {len(rows)} rows). Check the hgvs string against "
                "get_variant_scores, or pass a variant URN."
            )
        result: dict[str, Any] = {
            "urn": score_set_urn,
            "query_hgvs": hgvs.strip(),
            "columns": _columns,
            "matches": matches,
            "match_count": len(matches),
            "scanned_rows": len(rows),
        }
        calibrations = await self._raw_calibrations(score_set_urn)
        if calibrations:
            for match in matches:
                classified = classify_score(match.get("score"), calibrations)
                if classified:
                    match["classifications"] = classified
            result["calibrations"] = shape_calibrations(
                calibrations, full=response_mode in ("standard", "full")
            )
        return result

    async def _raw_calibrations(self, score_set_urn: str | None) -> list[dict[str, Any]]:
        """Best-effort fetch of a score set's raw ``scoreCalibrations`` (never raises).

        Classification enrichment must never fail the underlying score lookup, so
        any upstream error degrades to "no calibrations" rather than propagating.
        The score-set record read is cached, so this is usually free.
        """
        if not score_set_urn:
            return []
        try:
            record = await self._client.get_json(f"/score-sets/{score_set_urn}")
        except Exception:  # best-effort: a calibration miss must not fail the lookup
            return []
        cals = record.get("scoreCalibrations") if isinstance(record, dict) else None
        return cals if isinstance(cals, list) else []

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
        return {
            "gene": shaping.shape_gene(gene_raw, response_mode),
            "total_scored_variants": gene_raw.get("totalScoredVariants"),
            "score_sets": results,
            "coverage": coverage,
            **_page_block(total=total, returned=len(results), limit=capped, offset=offset),
        }

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
        total = len(items)
        page = items[offset : offset + capped]
        results = [shaping.shape_experiment(it, response_mode) for it in page]
        return {
            "query": text,
            "results": results,
            **_page_block(total=total, returned=len(results), limit=capped, offset=offset),
        }

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
        Rows are ordered by ``variant_urn`` so the page aligns with the
        ``get_variant_scores`` / ``get_variant_score`` accession column (DEF-5).
        """
        score_set_urn = validate_score_set_urn(urn)
        capped = _clamp(limit, 1, MAX_MAPPED_LIMIT)
        raw = await self._client.get_json(f"/score-sets/{score_set_urn}/mapped-variants")
        items = raw if isinstance(raw, list) else (raw.get("mappedVariants") or [])
        if current_only:
            items = [it for it in items if isinstance(it, dict) and it.get("current")]
        items = sorted(items, key=_mapped_variant_urn)
        total = len(items)
        page = items[offset : offset + capped]
        results = [shaping.shape_mapped_variant(it, response_mode) for it in page]
        return {
            "urn": score_set_urn,
            "mapped_variants": results,
            "current_only": current_only,
            "ordering": "variant_urn",
            **_page_block(total=total, returned=len(results), limit=capped, offset=offset),
        }

    async def find_variant(
        self,
        vrs_id: str,
        *,
        only_current: bool = True,
        enrich: bool = True,
        limit: int = DEFAULT_FIND_LIMIT,
        offset: int = 0,
        response_mode: str = shaping.DEFAULT_RESPONSE_MODE,
    ) -> dict[str, Any]:
        """Find a GA4GH VRS allele across every score set (delegated to resolvers)."""
        return await resolvers.find_variant(
            self._client,
            vrs_id,
            only_current=only_current,
            enrich=enrich,
            limit=limit,
            offset=offset,
            response_mode=response_mode,
        )

    async def get_hgvs_validation(self, variant: str) -> dict[str, Any]:
        """Validate an HGVS string upstream (delegated to resolvers)."""
        return await resolvers.get_hgvs_validation(self._client, variant)

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
        self, urn: str, *, response_mode: str = shaping.DEFAULT_RESPONSE_MODE
    ) -> dict[str, Any]:
        """Fetch a curated collection (``GET /collections/{urn}``)."""
        raw = await self._client.get_json(f"/collections/{urn.strip()}")
        return shaping.shape_collection(raw, response_mode)

    async def get_diagnostics(self) -> dict[str, Any]:
        """Report upstream reachability + version (never raises on upstream-down)."""
        diag: dict[str, Any] = {"base_url": self._client.base_url}
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
        return diag
