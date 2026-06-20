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
annotation index. Genes (rich HGNC identity), single ``/variants`` records, the
standard/full or current_only=False mapped-variant reads, hgvs validation and
calibration listings fall through to live, so a snapshot newer than the dump is
always reachable.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote

from mavedb_link.api.client import MaveDBClient
from mavedb_link.config import MaveDBApiConfig
from mavedb_link.data import provenance
from mavedb_link.data.repository import MirrorRepository

#: Sentinel: "the mirror does not answer this read" (vs a real ``None`` payload).
_MISS = object()


class HybridClient(MaveDBClient):
    """A live MaveDB client that serves what it can from the local mirror first."""

    def __init__(self, config: MaveDBApiConfig | None, *, repository: MirrorRepository) -> None:
        """Wrap the live client config and a read-only mirror repository."""
        super().__init__(config)
        self._repo = repository
        self._mirror_as_of = repository.meta().get("dump_as_of")

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
        """Serve POST /score-sets/search from the mirror (FTS), else live."""
        if path.strip("/") == "score-sets/search":
            body = json or {}
            records = self._repo.search_score_sets(
                body.get("text"), targets=body.get("targets"), authors=body.get("authors")
            )
            provenance.record("mirror", mirror_as_of=self._mirror_as_of)
            return {"scoreSets": records, "numScoreSets": len(records)}
        provenance.record("live")
        return await super().post_json(path, json=json, params=params)

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
            return None
        provenance.record("mirror", mirror_as_of=self._mirror_as_of)
        return _as_mapped_variants(rows)

    async def aclose(self) -> None:
        """Close the live client and the mirror connection."""
        await super().aclose()
        self._repo.close()

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
    return {
        "present": True,
        "as_of": meta.get("dump_as_of"),
        "zenodo_record": meta.get("zenodo_record"),
        "zenodo_version": meta.get("zenodo_version"),
        "score_set_count": meta.get("score_set_count"),
        "mapped_variant_count": meta.get("mapped_variant_count"),
        "built_utc": meta.get("build_utc"),
    }


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
