"""Mirror-first / live-backup data source.

``HybridClient`` is a drop-in :class:`MaveDBClient` subclass: it answers the
upstream reads it can from the local SQLite mirror and delegates everything else
(and any mirror-miss) to the live API via ``super()``. Because it IS a
``MaveDBClient``, the entire service / shaping / calibration stack consumes it
unchanged. Each answered read records its source (mirror | live) for ``_meta``.

Intercepted from the mirror: ``GET /score-sets/{urn}``, ``GET /experiments/{urn}``,
``GET /score-sets/{urn}/scores`` + ``/counts``, ``GET /mapped-variants/vrs/{id}``,
and ``POST /score-sets/search``; the per-set mapped-variant enumeration is served
via :meth:`score_set_mapped_variants` (current-only compact/minimal) from the same
annotation index. HGVS-first resolution is served via :meth:`vrs_for_hgvs` (the
hgvs_index) and a thin gene identity via :meth:`gene_identity`. Rich HGNC gene
identity, single ``/variants`` records, the standard/full or current_only=False
mapped-variant reads, hgvs validation and calibration listings fall through to
live, so a snapshot newer than the dump is always reachable.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import unquote

from mavedb_link.api.client import MaveDBClient
from mavedb_link.config import MaveDBApiConfig
from mavedb_link.data import provenance
from mavedb_link.data.mapped_cache import MappedVariantCache
from mavedb_link.data.repository import MirrorRepository

#: Sentinel: "the mirror does not answer this read" (vs a real ``None`` payload).
_MISS = object()


class HybridClient(MaveDBClient):
    """A live MaveDB client that serves what it can from the local mirror first."""

    def __init__(
        self,
        config: MaveDBApiConfig | None,
        *,
        repository: MirrorRepository,
        cache: MappedVariantCache | None = None,
    ) -> None:
        """Wrap the live client config and a read-only mirror repository."""
        super().__init__(config)
        self._repo = repository
        self._mirror_as_of = repository.meta().get("dump_as_of")
        self._mapped_cache = cache
        self._mapped_inflight: dict[str, asyncio.Lock] = {}

    async def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        """Serve a GET-JSON read from the mirror when possible, else live."""
        hit = self._mirror_json(path, params)
        if hit is not _MISS:
            provenance.record("mirror", mirror_as_of=self._mirror_as_of)
            return hit
        provenance.record("live")
        return await super().get_json(path, params=params)

    async def get_text(
        self, path: str, *, params: dict[str, Any] | None = None, accept: str = "text/csv"
    ) -> str:
        """Serve a GET-text (CSV) read from the mirror when possible, else live."""
        hit = self._mirror_text(path, params)
        if hit is not None:
            provenance.record("mirror", mirror_as_of=self._mirror_as_of)
            return hit
        provenance.record("live")
        return await super().get_text(path, params=params, accept=accept)

    async def post_json(
        self, path: str, *, json: Any | None = None, params: dict[str, Any] | None = None
    ) -> Any:
        """Serve POST /score-sets/search and the experiments browse from the mirror.

        The unfiltered experiments browse (``/experiments/search`` with no text and
        no authors) is the only search variant the upstream endpoint answers slowly
        (~30s, the FULL published list, unpaged), so it is served from the local
        ``experiment`` table -- fast and offline. A text/author experiment search
        stays live (scoped, faithful to the upstream matcher).
        """
        if path.strip("/") == "score-sets/search":
            body = json or {}
            records = self._repo.search_score_sets(
                body.get("text"), targets=body.get("targets"), authors=body.get("authors")
            )
            provenance.record("mirror", mirror_as_of=self._mirror_as_of)
            return {"scoreSets": records, "numScoreSets": len(records)}
        if path.strip("/") == "experiments/search":
            body = json or {}
            if not body.get("text") and not body.get("authors"):
                records = self._repo.all_experiments()
                provenance.record("mirror", mirror_as_of=self._mirror_as_of)
                return {"experiments": records, "numExperiments": len(records)}
        provenance.record("live")
        return await super().post_json(path, json=json, params=params)

    def facet_vocabularies(self) -> dict[str, set[str]]:
        """Corpus facet vocabularies (targets/organisms/authors) from the mirror."""
        return self._repo.facet_vocabularies()

    def mirror_meta(self) -> dict[str, Any]:
        """The mirror's provenance row (snapshot date, counts) for diagnostics."""
        return self._repo.meta()

    def score_set_mapped_variants(self, score_set_urn: str) -> list[dict[str, Any]] | None:
        """Upstream-shaped CURRENT mapped variants for a score set, or None on miss.

        Serves the per-set mapped-variant enumeration from the annotation index
        (GAP-B) so ``get_mapped_variants`` need not hit the slow live endpoint. The
        index carries only current mappings + the compact identity fields, so the
        caller must restrict this to a current-only compact/minimal read (the
        service does). Returns None when the snapshot has no mapping for the set --
        mappings can post-date the dump, so an empty list would falsely claim
        "none"; the caller then falls through to the authoritative live endpoint.
        """
        rows = self._repo.mapped_by_score_set(score_set_urn)
        if not rows:
            cached = self._mapped_cache_get(score_set_urn)
            if cached is None:
                return None
            provenance.record("live")
            return [it for it in cached if isinstance(it, dict) and it.get("current")]
        provenance.record("mirror", mirror_as_of=self._mirror_as_of)
        return _as_mapped_variants(rows)

    def mapped_vrs_for_variant(self, variant_urn: str) -> str | None:
        """The genome-mapped VRS allele id for a variant URN from the mirror, or None.

        Lets find_variant(variant_urn=) resolve the VRS without a live variant fetch
        (D.3); the id is identical to the live one (same digest), so provenance/latency
        change but the rollup shape does not. None on miss -> caller goes live.
        """
        for row in self._repo.mapped_by_variant_urn(variant_urn):
            vrs = row.get("vrs_id")
            if vrs:
                provenance.record("mirror", mirror_as_of=self._mirror_as_of)
                return str(vrs)
        score_set_urn = _score_set_urn_for_variant(variant_urn)
        cached = self._mapped_cache_get(score_set_urn) if score_set_urn else None
        vrs = _vrs_for_variant(cached or [], variant_urn)
        if vrs:
            provenance.record("live")
            return vrs
        return None

    def vrs_for_hgvs(
        self, core: str, full: str | None = None, *, gene: str | None = None
    ) -> list[dict[str, Any]]:
        """Resolve an HGVS (``core`` body + ``full`` accessioned form) to rows.

        Lets find_variant(hgvs=) resolve VRS without probing the live API. Returns
        ``[]`` on miss (no mirror coverage) so the caller falls through to the live
        probe. Records mirror provenance only when it actually answers.
        """
        rows = [r for r in self._repo.resolve_hgvs(core, full, gene=gene) if r.get("vrs_id")]
        if rows:
            provenance.record("mirror", mirror_as_of=self._mirror_as_of)
        cached = self._cached_vrs_for_hgvs(core, gene=gene)
        if cached:
            provenance.record("live")
        return _dedupe_hgvs_rows([*rows, *cached])

    def gene_identity(self, symbol: str) -> dict[str, Any] | None:
        """Thin gene identity (symbol + organism) from the mirror index, or None."""
        ident = self._repo.gene_identity(symbol)
        if ident is not None:
            provenance.record("mirror", mirror_as_of=self._mirror_as_of)
        return ident

    def score_set_urns_for_gene(self, symbol: str) -> list[str]:
        """Score-set URNs for a gene from the mirror index, ordered."""
        urns = self._repo.gene_score_set_urns(symbol)
        if urns:
            provenance.record("mirror", mirror_as_of=self._mirror_as_of)
        return urns

    async def ensure_mapped_variants(self, score_set_urn: str) -> list[dict[str, Any]]:
        """Return raw mapped variants, lazily fetching and caching by score set.

        Cache rows are live-authority data, so cache hits record ``data_source=live``.
        Only an explicit mirror ``mappingState == "failed"`` short-circuits to a
        cached empty list; every other state falls through to the live API and lets
        live errors propagate exactly as the base client does.
        """
        if self._mapped_cache is None:
            return await self._fetch_live_mapped_variants(score_set_urn)
        hit = self._mapped_cache_get(score_set_urn)
        if hit is not None:
            provenance.record("live")
            return hit
        lock = self._mapped_inflight.setdefault(score_set_urn, asyncio.Lock())
        try:
            async with lock:
                hit = self._mapped_cache_get(score_set_urn)
                if hit is not None:
                    provenance.record("live")
                    return hit
                record = self._repo.score_set_record(score_set_urn)
                if isinstance(record, dict) and record.get("mappingState") == "failed":
                    items: list[dict[str, Any]] = []
                else:
                    items = await self._fetch_live_mapped_variants(score_set_urn)
                self._mapped_cache_put(score_set_urn, items)
                return items
        finally:
            if self._mapped_inflight.get(score_set_urn) is lock and not lock.locked():
                self._mapped_inflight.pop(score_set_urn, None)

    def mapped_cache_stats(self) -> dict[str, Any] | None:
        """Diagnostics for the mapped-variant cache, or None when disabled/broken."""
        if self._mapped_cache is None:
            return None
        try:
            return self._mapped_cache.stats()
        except Exception:
            return None

    async def aclose(self) -> None:
        """Close the live client and the mirror connection."""
        await super().aclose()
        self._repo.close()
        if self._mapped_cache is not None:
            self._mapped_cache.close()

    async def _fetch_live_mapped_variants(self, score_set_urn: str) -> list[dict[str, Any]]:
        provenance.record("live")
        raw = await super().get_json(f"/score-sets/{score_set_urn}/mapped-variants")
        return _as_mapped_list(raw)

    def _mapped_cache_get(self, score_set_urn: str) -> list[dict[str, Any]] | None:
        if self._mapped_cache is None:
            return None
        try:
            return self._mapped_cache.get(score_set_urn)
        except Exception:
            return None

    def _mapped_cache_put(self, score_set_urn: str, items: list[dict[str, Any]]) -> None:
        if self._mapped_cache is None:
            return
        try:
            self._mapped_cache.put(score_set_urn, items)
        except Exception:
            return

    def _cached_vrs_for_hgvs(self, core: str, *, gene: str | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for candidate in self._repo.hgvs_variant_urns(core, gene=gene):
            variant_urn = str(candidate.get("variant_urn") or "")
            score_set_urn = str(candidate.get("score_set_urn") or "")
            cached = self._mapped_cache_get(score_set_urn)
            if cached is None:
                continue
            vrs = _vrs_for_variant(cached, variant_urn)
            if vrs:
                rows.append(
                    {
                        "variant_urn": variant_urn,
                        "score_set_urn": score_set_urn,
                        "vrs_id": vrs,
                    }
                )
        return rows

    # --- mirror routing -------------------------------------------------------

    def _mirror_json(self, path: str, params: dict[str, Any] | None) -> Any:
        """Return a mirror JSON payload, or ``_MISS`` to defer to live."""
        parts = path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "score-sets":
            record = self._repo.score_set_record(parts[1])
            return record if record is not None else _MISS
        if len(parts) == 2 and parts[0] == "experiments" and parts[1] != "search":
            record = self._repo.experiment_record(parts[1])
            return record if record is not None else _MISS
        if len(parts) == 3 and parts[0] == "mapped-variants" and parts[1] == "vrs":
            items = self._repo.mapped_by_vrs(unquote(parts[2]))
            return _as_mapped_variants(items) if items else _MISS
        return _MISS

    def _mirror_text(self, path: str, params: dict[str, Any] | None) -> str | None:
        """Return a mirror CSV page, or ``None`` to defer to live."""
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "score-sets" and parts[2] in ("scores", "counts"):
            start = int((params or {}).get("start", 0))
            limit = int((params or {}).get("limit", 1000))
            if parts[2] == "scores":
                return self._repo.scores_csv(parts[1], start=start, limit=limit)
            return self._repo.counts_csv(parts[1], start=start, limit=limit)
        return None


def mirror_status(client: object) -> dict[str, Any]:
    """Diagnostics block for the mirror behind ``client`` (present=False if none).

    Duck-typed on ``mirror_meta`` so a plain live client reports no mirror.
    """
    meta_fn = getattr(client, "mirror_meta", None)
    if not callable(meta_fn):
        return {"present": False}
    meta = meta_fn()
    status = {
        "present": True,
        "as_of": meta.get("dump_as_of"),
        "zenodo_record": meta.get("zenodo_record"),
        "zenodo_version": meta.get("zenodo_version"),
        "score_set_count": meta.get("score_set_count"),
        "mapped_variant_count": meta.get("mapped_variant_count"),
        "built_utc": meta.get("build_utc"),
    }
    mapping_coverage = _mapping_coverage(meta.get("mapping_coverage_json"))
    if mapping_coverage is not None:
        status["mapping_coverage"] = mapping_coverage
    return status


def mapped_cache_status(client: object) -> dict[str, Any]:
    """Diagnostics block for the mapped-variant cache behind ``client``."""
    stats_fn = getattr(client, "mapped_cache_stats", None)
    if not callable(stats_fn):
        return {"enabled": False}
    stats = stats_fn()
    if not isinstance(stats, dict):
        return {"enabled": False}
    return {"enabled": True, **stats}


def _as_mapped_variants(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct the upstream mapped-variant shape from mirror identity rows."""
    return [
        {
            "variantUrn": row.get("variant_urn"),
            "postMapped": {"id": row.get("vrs_id")},
            "clingenAlleleId": row.get("clingen_allele_id"),
            "current": True,
        }
        for row in rows
    ]


def _mapping_coverage(raw: Any) -> dict[str, int] | None:
    """Decode the mirror meta mapping coverage JSON."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    keys = ("complete", "incomplete", "failed", "none")
    return {key: int(data.get(key, 0) or 0) for key in keys}


def _as_mapped_list(raw: Any) -> list[dict[str, Any]]:
    """Normalise a ``/mapped-variants`` response to raw dict records."""
    items = (
        raw
        if isinstance(raw, list)
        else (raw.get("mappedVariants") if isinstance(raw, dict) else None)
    )
    return [it for it in (items or []) if isinstance(it, dict)]


def _score_set_urn_for_variant(variant_urn: str) -> str | None:
    """Return the score-set URN segment of ``urn:mavedb:...#n``."""
    if "#" not in variant_urn:
        return None
    return variant_urn.split("#", 1)[0]


def _vrs_for_variant(items: list[dict[str, Any]], variant_urn: str) -> str | None:
    """Find a variant's post-mapped VRS id in raw mapped-variant records."""
    for item in items:
        if item.get("variantUrn") != variant_urn:
            continue
        post_mapped = item.get("postMapped")
        if isinstance(post_mapped, dict) and post_mapped.get("id"):
            return str(post_mapped["id"])
    return None


def _dedupe_hgvs_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe VRS rows while preserving mirror/cache field shape."""
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        variant_urn = str(row.get("variant_urn") or "")
        score_set_urn = str(row.get("score_set_urn") or "")
        vrs_id = str(row.get("vrs_id") or "")
        if variant_urn and score_set_urn and vrs_id:
            out[(score_set_urn, variant_urn, vrs_id)] = {
                "variant_urn": variant_urn,
                "score_set_urn": score_set_urn,
                "vrs_id": vrs_id,
            }
    return [out[k] for k in sorted(out)]
