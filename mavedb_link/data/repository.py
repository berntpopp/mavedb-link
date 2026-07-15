"""Read-only repository over the local SQLite mirror (data plane).

Opens the mirror read-only and answers the upstream-shaped reads the P3 client
shim intercepts: score-set/experiment records (verbatim camelCase), paged
score/count CSVs (denamespaced to the live header), precomputed distributions,
the cross-dataset mapped-variant identity index, and FTS over score sets. A
missing record returns ``None``/``[]`` (a mirror-miss), never an exception — the
shim falls through to the live API on ``None``.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from mavedb_link.constants import MIRROR_SCHEMA_VERSION as SCHEMA_VERSION

#: FTS5 token pattern (alphanumerics; gene symbols, accessions, words).
_TOKEN = re.compile(r"[A-Za-z0-9]+")


class MirrorRepository:
    """Read-only accessor over a built mirror database."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an open read-only SQLite connection."""
        self._con = connection
        self._con.row_factory = sqlite3.Row
        self._facet_vocab: dict[str, set[str]] | None = None

    @classmethod
    def open(cls, db_path: Path | str) -> MirrorRepository | None:
        """Open the mirror read-only; return None if absent or schema-incompatible."""
        path = Path(db_path)
        if not path.exists():
            return None
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute("SELECT schema_version FROM meta WHERE id = 1").fetchone()
        except sqlite3.Error:
            con.close()
            return None
        if row is None or row["schema_version"] != SCHEMA_VERSION:
            con.close()
            return None
        return cls(con)

    def close(self) -> None:
        """Close the underlying connection (idempotent)."""
        self._con.close()

    # --- provenance -----------------------------------------------------------

    def meta(self) -> dict[str, Any]:
        """The single provenance row as a dict (dump_as_of, counts, zenodo_record…)."""
        row = self._con.execute("SELECT * FROM meta WHERE id = 1").fetchone()
        return dict(row) if row is not None else {}

    # --- records --------------------------------------------------------------

    def score_set_record(self, urn: str) -> dict[str, Any] | None:
        """The upstream-shaped score-set record, or None on miss."""
        return self._record("score_set", urn)

    def experiment_record(self, urn: str) -> dict[str, Any] | None:
        """The upstream-shaped experiment record, or None on miss."""
        return self._record("experiment", urn)

    def experiment_set_record(self, urn: str) -> dict[str, Any] | None:
        """The upstream-shaped experiment-set record, or None on miss."""
        return self._record("experiment_set", urn)

    def all_experiments(self) -> list[dict[str, Any]]:
        """Every mirrored (published) experiment record, ordered by URN.

        Serves the unfiltered experiments browse from the local table so it does
        not hit the slow, unpaged live ``/experiments/search``.
        """
        rows = self._con.execute("SELECT record_json FROM experiment ORDER BY urn").fetchall()
        return [json.loads(r["record_json"]) for r in rows]

    def has_score_set(self, urn: str) -> bool:
        """Whether a score set is present in this snapshot."""
        return (
            self._con.execute("SELECT 1 FROM score_set WHERE urn = ?", (urn,)).fetchone()
            is not None
        )

    def _record(self, table: str, urn: str) -> dict[str, Any] | None:
        row = self._con.execute(
            f"SELECT record_json FROM {table} WHERE urn = ?",  # noqa: S608 (fixed table set)
            (urn,),
        ).fetchone()
        if row is None:
            return None
        loaded: dict[str, Any] = json.loads(row["record_json"])
        return loaded

    # --- CSV pages ------------------------------------------------------------

    def scores_csv(self, urn: str, *, start: int, limit: int) -> str | None:
        """A paged scores CSV (header + rows[start:start+limit]), or None on miss."""
        return self._csv_page(urn, "scores_csv", start, limit)

    def counts_csv(self, urn: str, *, start: int, limit: int) -> str | None:
        """A paged counts CSV, or None on miss/absent."""
        return self._csv_page(urn, "counts_csv", start, limit)

    def _csv_page(self, urn: str, column: str, start: int, limit: int) -> str | None:
        row = self._con.execute(
            f"SELECT {column} AS csv FROM score_set_data WHERE urn = ?",  # noqa: S608 (fixed cols)
            (urn,),
        ).fetchone()
        if row is None or row["csv"] is None:
            return None
        return _page_csv(row["csv"], start, limit)

    # --- distribution ---------------------------------------------------------

    def distribution(self, urn: str) -> dict[str, Any] | None:
        """The precomputed distribution summary for a set, or None on miss."""
        row = self._con.execute(
            "SELECT n, min, max, mean, histogram_json, quantiles_json "
            "FROM score_distribution WHERE score_set_urn = ?",
            (urn,),
        ).fetchone()
        if row is None:
            return None
        return {
            "n": row["n"],
            "min": row["min"],
            "max": row["max"],
            "mean": row["mean"],
            "histogram": json.loads(row["histogram_json"]),
            "quantiles": json.loads(row["quantiles_json"]),
        }

    # --- mapped-variant identity (cross-dataset) ------------------------------

    def mapped_by_vrs(self, vrs_id: str) -> list[dict[str, Any]]:
        """Mapped-variant rows for a VRS id (across every score set)."""
        return self._mapped("vrs_id", vrs_id)

    def mapped_by_clingen(self, clingen_allele_id: str) -> list[dict[str, Any]]:
        """Mapped-variant rows for a ClinGen allele id (across every score set)."""
        return self._mapped("clingen_allele_id", clingen_allele_id)

    def mapped_by_variant_urn(self, variant_urn: str) -> list[dict[str, Any]]:
        """Mapped-variant rows for a single variant URN."""
        return self._mapped("variant_urn", variant_urn)

    def mapped_by_score_set(self, score_set_urn: str) -> list[dict[str, Any]]:
        """Every (current) mapped-variant row for one score set (the per-set enum)."""
        return self._mapped("score_set_urn", score_set_urn)

    def _mapped(self, column: str, value: str) -> list[dict[str, Any]]:
        rows = self._con.execute(
            f"SELECT * FROM mapped_variant WHERE {column} = ? "  # noqa: S608 (fixed column set)
            "ORDER BY score_set_urn, variant_urn",
            (value,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- gene + search --------------------------------------------------------

    def gene_score_set_urns(self, symbol: str) -> list[str]:
        """Score-set URNs targeting a gene symbol (case-insensitive), ordered."""
        rows = self._con.execute(
            "SELECT DISTINCT score_set_urn FROM gene_index WHERE gene_symbol_upper = ? "
            "ORDER BY score_set_urn",
            (symbol.strip().upper(),),
        ).fetchall()
        return [r["score_set_urn"] for r in rows]

    def gene_score_sets(self, symbol: str) -> list[dict[str, Any]]:
        """Full score-set records targeting a gene symbol, ordered by URN."""
        return self._records_for(self.gene_score_set_urns(symbol))

    def gene_identity(self, symbol: str) -> dict[str, Any] | None:
        """Thin gene identity from the index (symbol + organism), or None if absent."""
        row = self._con.execute(
            "SELECT gene_symbol, organism FROM gene_index WHERE gene_symbol_upper = ? LIMIT 1",
            (symbol.strip().upper(),),
        ).fetchone()
        if row is None:
            return None
        out: dict[str, Any] = {"symbol": row["gene_symbol"]}
        if row["organism"]:
            out["organism"] = row["organism"]
        return out

    def hgvs_variant_urns(self, core: str, *, gene: str | None = None) -> list[dict[str, Any]]:
        """Variant URNs whose target-relative HGVS ``core`` matches, from hgvs_index.

        The VRS-less arm of :meth:`resolve_hgvs`: returns ``(variant_urn,
        score_set_urn)`` rows for the lazy mapped-variant cache to fill the VRS from,
        since the dump-omitted ``mapped_variant`` table is empty. ``core`` is the
        lowercased, prefix-stripped body (:func:`scores.hgvs_core`); scoped by gene
        when given.
        """
        core_v = core.strip()
        if not core_v:
            return []
        if gene and gene.strip():
            rows = self._con.execute(
                "SELECT DISTINCT h.variant_urn, h.score_set_urn FROM hgvs_index h "
                "JOIN gene_index g ON g.score_set_urn = h.score_set_urn "
                "WHERE g.gene_symbol_upper = ? AND (h.hgvs_nt = ? OR h.hgvs_pro = ? "
                "OR h.hgvs_splice = ?)",
                (gene.strip().upper(), core_v, core_v, core_v),
            ).fetchall()
        else:
            rows = self._con.execute(
                "SELECT DISTINCT variant_urn, score_set_urn FROM hgvs_index "
                "WHERE hgvs_nt = ? OR hgvs_pro = ? OR hgvs_splice = ?",
                (core_v, core_v, core_v),
            ).fetchall()
        return [dict(r) for r in rows]

    def resolve_hgvs(
        self, core: str, full: str | None = None, *, gene: str | None = None
    ) -> list[dict[str, Any]]:
        """Resolve an HGVS to (variant_urn, score_set_urn, vrs_id) rows.

        Two paths, unioned: target-relative HGVS via ``hgvs_index`` matched on
        ``core`` (the accession-prefix-stripped, lowercased body — what the index
        stores), scoped by ``gene`` when given; and genomic/accessioned HGVS via the
        post-mapped HGVS columns on ``mapped_variant`` matched on ``full`` (the whole
        accessioned string, since those columns keep the accession), case-insensitive.
        ``core``/``full`` come from the caller (:func:`scores.hgvs_core` + lowercase).
        """
        core_v = core.strip()
        full_v = (full or core).strip().lower()
        if not core_v:
            return []
        out: dict[tuple[str | None, str | None], dict[str, Any]] = {}
        if gene and gene.strip():  # target-relative: hgvs_index -> mapped_variant VRS
            sql = (
                "SELECT h.variant_urn, h.score_set_urn, m.vrs_id FROM hgvs_index h "
                "JOIN gene_index g ON g.score_set_urn = h.score_set_urn "
                "LEFT JOIN mapped_variant m ON m.variant_urn = h.variant_urn "
                "WHERE g.gene_symbol_upper = ? AND (h.hgvs_nt = ? OR h.hgvs_pro = ? "
                "OR h.hgvs_splice = ?)"
            )
            params: tuple[Any, ...] = (gene.strip().upper(), core_v, core_v, core_v)
        else:
            sql = (
                "SELECT h.variant_urn, h.score_set_urn, m.vrs_id FROM hgvs_index h "
                "LEFT JOIN mapped_variant m ON m.variant_urn = h.variant_urn "
                "WHERE h.hgvs_nt = ? OR h.hgvs_pro = ? OR h.hgvs_splice = ?"
            )
            params = (core_v, core_v, core_v)
        for r in self._con.execute(sql, params).fetchall():
            out[(r["variant_urn"], r["vrs_id"])] = dict(r)
        for r in self._con.execute(  # genomic/accessioned: post-mapped HGVS columns
            "SELECT variant_urn, score_set_urn, vrs_id FROM mapped_variant WHERE "
            "post_mapped_hgvs_g = ? COLLATE NOCASE OR post_mapped_hgvs_c = ? COLLATE NOCASE "
            "OR post_mapped_hgvs_p = ? COLLATE NOCASE",
            (full_v, full_v, full_v),
        ).fetchall():
            out.setdefault((r["variant_urn"], r["vrs_id"]), dict(r))
        return sorted(
            out.values(),
            key=lambda d: (d.get("score_set_urn") or "", d.get("variant_urn") or ""),
        )

    def search_score_sets(
        self,
        text: str | None,
        *,
        targets: list[str] | None = None,
        authors: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Candidate score-set records for a text/target/author query (FTS + filters).

        Returns the full candidate set ordered by URN; ranking/faceting/paging stay
        in the service so mirror and live behave identically downstream.
        """
        urns: set[str] | None = None
        if text and text.strip():
            urns = set(self._fts_urns(text))
        if targets:
            target_urns: set[str] = set()
            for symbol in targets:
                target_urns.update(self.gene_score_set_urns(symbol))
            urns = target_urns if urns is None else (urns & target_urns)
        if urns is None:
            urns = set(self._all_score_set_urns())
        records = self._records_for(sorted(urns))
        if authors:
            wanted = [a.strip().lower() for a in authors if a.strip()]
            records = [r for r in records if _has_author(r, wanted)]
        return records

    def facet_vocabularies(self) -> dict[str, set[str]]:
        """Corpus vocabularies for the search facets (cached, computed once).

        Returns the exact value sets each facet matches against, so the search
        service can reject a value that can never match (an ``invalid_input``,
        never a silent-empty result) instead of returning zero rows: ``targets``
        (upper-cased gene symbols, as ``gene_index`` matches them), ``organisms``
        (lower-cased organism names), and ``authors`` (lower-cased primary-
        publication author names, the field ``_has_author`` matches on).
        """
        if self._facet_vocab is None:
            targets = {
                r["gene_symbol_upper"]
                for r in self._con.execute("SELECT DISTINCT gene_symbol_upper FROM gene_index")
                if r["gene_symbol_upper"]
            }
            organisms = {
                str(r["organism"]).strip().lower()
                for r in self._con.execute("SELECT DISTINCT organism FROM gene_index")
                if r["organism"]
            }
            authors: set[str] = set()
            for (blob,) in self._con.execute("SELECT record_json FROM score_set"):
                record = json.loads(blob)
                for pub in record.get("primaryPublicationIdentifiers") or []:
                    for author in pub.get("authors") or []:
                        if author.get("name"):
                            authors.add(str(author["name"]).strip().lower())
            self._facet_vocab = {
                "targets": targets,
                "organisms": organisms,
                "authors": authors,
            }
        return self._facet_vocab

    def _fts_urns(self, text: str) -> list[str]:
        tokens = _TOKEN.findall(text)
        if not tokens:
            return []
        query = " OR ".join(f"{t}*" for t in tokens)
        try:
            rows = self._con.execute(
                "SELECT urn FROM score_set_fts WHERE score_set_fts MATCH ? ORDER BY rank", (query,)
            ).fetchall()
        except sqlite3.Error:
            return []
        return [r["urn"] for r in rows]

    def _all_score_set_urns(self) -> list[str]:
        rows = self._con.execute("SELECT urn FROM score_set ORDER BY urn").fetchall()
        return [r["urn"] for r in rows]

    def _records_for(self, urns: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for urn in urns:
            record = self.score_set_record(urn)
            if record is not None:
                out.append(record)
        return out


def _page_csv(csv_text: str, start: int, limit: int) -> str:
    """Slice a stored CSV to one page, always re-emitting the header row."""
    lines = csv_text.splitlines()
    if not lines:
        return csv_text
    header, rows = lines[0], lines[1:]
    page = rows[start : start + limit]
    return "\n".join([header, *page]) + "\n"


def _has_author(record: dict[str, Any], wanted_lower: list[str]) -> bool:
    """Whether any wanted author substring appears in a record's primary authors."""
    names: list[str] = []
    for pub in record.get("primaryPublicationIdentifiers") or []:
        for author in pub.get("authors") or []:
            if author.get("name"):
                names.append(str(author["name"]).lower())
    blob = " ".join(names)
    return any(w in blob for w in wanted_lower)
