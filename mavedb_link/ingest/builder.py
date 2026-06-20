"""Build the local SQLite mirror from a MaveDB bulk-dump zip.

Streams ``main.json`` (nested experimentSets -> experiments -> scoreSets) and the
per-set ``csv/`` members into a fresh SQLite database, then atomically swaps it
into place (``os.replace``) so readers never observe a half-built file. Records
are stored as the upstream camelCase JSON (the shapers consume them unchanged);
nested score sets/experiments are enriched with their parent URNs to match the
live record shape.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from mavedb_link.constants import MIRROR_SCHEMA_VERSION as SCHEMA_VERSION
from mavedb_link.ingest.parsing import (
    compute_distribution,
    denamespace_csv,
    extract_scores,
    parse_annotations,
)


def _schema_sql() -> str:
    """Load the bundled schema DDL."""
    return resources.files("mavedb_link.data").joinpath("schema.sql").read_text(encoding="utf-8")


def _csv_member(urn: str, suffix: str) -> str:
    """Dump CSV member name for a score set URN (``urn:`` -> ``-`` in filenames)."""
    return f"csv/{urn.replace(':', '-')}.{suffix}.csv"


def _target_genes(score_set: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a score set's target genes into ``gene_index`` row dicts."""
    rows: list[dict[str, Any]] = []
    for target in score_set.get("targetGenes") or []:
        name = target.get("name")
        if not name:
            continue
        taxonomy = (target.get("targetSequence") or {}).get("taxonomy") or {}
        rows.append(
            {
                "gene_symbol_upper": str(name).upper(),
                "gene_symbol": name,
                "score_set_urn": score_set["urn"],
                "organism": taxonomy.get("organismName"),
                "category": target.get("category"),
            }
        )
    return rows


def _fts_fields(score_set: dict[str, Any]) -> tuple[str, str]:
    """The (genes, authors) free-text blobs for the FTS row."""
    genes = " ".join(
        str(t.get("name")) for t in (score_set.get("targetGenes") or []) if t.get("name")
    )
    authors: list[str] = []
    for pub in score_set.get("primaryPublicationIdentifiers") or []:
        for author in pub.get("authors") or []:
            if author.get("name"):
                authors.append(str(author["name"]))
    return genes, " ".join(authors)


def _insert_score_set(
    con: sqlite3.Connection,
    zf: zipfile.ZipFile,
    zip_names: set[str],
    score_set: dict[str, Any],
) -> int:
    """Insert one score set + its CSV blobs/derived rows. Returns mapped-variant count."""
    urn = score_set["urn"]
    has_cal = 1 if score_set.get("scoreCalibrations") else 0
    con.execute(
        "INSERT INTO score_set (urn, experiment_urn, experiment_set_urn, title, "
        "short_description, license, num_variants, published_date, has_calibrations, "
        "record_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            urn,
            (score_set.get("experiment") or {}).get("urn"),
            score_set.get("experimentSetUrn"),
            score_set.get("title"),
            score_set.get("shortDescription"),
            (score_set.get("license") or {}).get("shortName"),
            score_set.get("numVariants"),
            score_set.get("publishedDate"),
            has_cal,
            json.dumps(score_set),
        ),
    )
    genes, authors = _fts_fields(score_set)
    con.execute(
        "INSERT INTO score_set_fts (urn, title, short_description, genes, authors) "
        "VALUES (?,?,?,?,?)",
        (
            urn,
            score_set.get("title") or "",
            score_set.get("shortDescription") or "",
            genes,
            authors,
        ),
    )
    gene_rows = _target_genes(score_set)
    if gene_rows:
        con.executemany(
            "INSERT INTO gene_index (gene_symbol_upper, gene_symbol, score_set_urn, organism, "
            "category) VALUES (:gene_symbol_upper, :gene_symbol, :score_set_urn, :organism, "
            ":category)",
            gene_rows,
        )

    scores_csv = _read_member(zf, zip_names, urn, "scores")
    counts_csv = _read_member(zf, zip_names, urn, "counts")
    annotations_csv = _read_member(zf, zip_names, urn, "annotations")
    if scores_csv is not None or counts_csv is not None or annotations_csv is not None:
        con.execute(
            "INSERT INTO score_set_data (urn, scores_csv, counts_csv, annotations_csv) "
            "VALUES (?,?,?,?)",
            (urn, scores_csv, counts_csv, annotations_csv),
        )
    if scores_csv is not None:
        dist = compute_distribution(extract_scores(scores_csv))
        con.execute(
            "INSERT INTO score_distribution (score_set_urn, n, min, max, mean, histogram_json, "
            "quantiles_json) VALUES (?,?,?,?,?,?,?)",
            (
                urn,
                dist["n"],
                dist["min"],
                dist["max"],
                dist["mean"],
                json.dumps(dist["histogram"]),
                json.dumps(dist["quantiles"]),
            ),
        )
    mapped_count = 0
    if annotations_csv is not None:
        mapped = parse_annotations(annotations_csv, urn)
        if mapped:
            con.executemany(
                "INSERT INTO mapped_variant (variant_urn, score_set_urn, vrs_id, "
                "clingen_allele_id, post_mapped_hgvs_g, post_mapped_hgvs_p, post_mapped_hgvs_c) "
                "VALUES (:variant_urn, :score_set_urn, :vrs_id, :clingen_allele_id, "
                ":post_mapped_hgvs_g, :post_mapped_hgvs_p, :post_mapped_hgvs_c)",
                mapped,
            )
            mapped_count = len(mapped)
    return mapped_count


def _read_member(zf: zipfile.ZipFile, zip_names: set[str], urn: str, suffix: str) -> str | None:
    """Read one CSV member on demand (denamespaced), or None if absent.

    Read per-member rather than caching the whole dump, so peak memory stays at
    one CSV -- the real dump is ~1.8 GB uncompressed across thousands of files.
    """
    name = _csv_member(urn, suffix)
    if name not in zip_names:
        return None
    return denamespace_csv(zf.read(name).decode("utf-8"))


def build_database(
    dump_path: Path,
    db_path: Path,
    *,
    source_md5: str | None = None,
    source_url: str | None = None,
    zenodo_record: str | None = None,
    zenodo_version: str | None = None,
) -> dict[str, Any]:
    """Build ``db_path`` from the dump zip atomically; return a provenance summary."""
    started = time.monotonic()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=db_path.parent, suffix=".sqlite.tmp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with zipfile.ZipFile(dump_path) as zf:
            zip_names = set(zf.namelist())
            main = json.loads(zf.read("main.json"))
            summary = _populate(tmp_path, main, zf, zip_names, started)
        _write_meta(
            tmp_path, summary, source_md5, source_url, zenodo_record, zenodo_version, started
        )
        os.replace(tmp_path, db_path)
        return summary
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _populate(
    tmp_path: Path,
    main: dict[str, Any],
    zf: zipfile.ZipFile,
    zip_names: set[str],
    started: float,
) -> dict[str, Any]:
    """Create the schema and insert every record; return the counts summary."""
    con = sqlite3.connect(tmp_path)
    try:
        con.execute("PRAGMA journal_mode = OFF")
        con.execute("PRAGMA synchronous = OFF")
        con.executescript(_schema_sql())
        es_count = exp_count = ss_count = mapped_count = 0
        for es in main.get("experimentSets") or []:
            es_count += 1
            con.execute(
                "INSERT INTO experiment_set (urn, title, record_json) VALUES (?,?,?)",
                (es.get("urn"), es.get("title"), json.dumps(_strip_children(es, "experiments"))),
            )
            for exp in es.get("experiments") or []:
                exp_count += 1
                exp = {**exp, "experimentSetUrn": es.get("urn")}
                con.execute(
                    "INSERT INTO experiment (urn, experiment_set_urn, title, short_description, "
                    "record_json) VALUES (?,?,?,?,?)",
                    (
                        exp.get("urn"),
                        es.get("urn"),
                        exp.get("title"),
                        exp.get("shortDescription"),
                        json.dumps(_strip_children(exp, "scoreSets")),
                    ),
                )
                for score_set in exp.get("scoreSets") or []:
                    ss_count += 1
                    enriched = {
                        **score_set,
                        "experiment": {"urn": exp.get("urn")},
                        "experimentSetUrn": es.get("urn"),
                    }
                    mapped_count += _insert_score_set(con, zf, zip_names, enriched)
        con.commit()
        con.execute("INSERT INTO score_set_fts (score_set_fts) VALUES ('optimize')")
        con.commit()
    finally:
        con.close()
    return {
        "dump_as_of": main.get("asOf"),
        "experiment_set_count": es_count,
        "experiment_count": exp_count,
        "score_set_count": ss_count,
        "mapped_variant_count": mapped_count,
        "schema_version": SCHEMA_VERSION,
    }


def _strip_children(record: dict[str, Any], child_key: str) -> dict[str, Any]:
    """A copy of a parent record without its (separately-stored) child collection."""
    return {k: v for k, v in record.items() if k != child_key}


def _write_meta(
    tmp_path: Path,
    summary: dict[str, Any],
    source_md5: str | None,
    source_url: str | None,
    zenodo_record: str | None,
    zenodo_version: str | None,
    started: float,
) -> None:
    """Write the single provenance row."""
    con = sqlite3.connect(tmp_path)
    try:
        con.execute(
            "INSERT INTO meta (id, schema_version, dump_as_of, zenodo_record, zenodo_version, "
            "source_url, source_md5, experiment_set_count, experiment_count, score_set_count, "
            "mapped_variant_count, build_utc, build_duration_s) "
            "VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                SCHEMA_VERSION,
                summary["dump_as_of"],
                zenodo_record,
                zenodo_version,
                source_url,
                source_md5,
                summary["experiment_set_count"],
                summary["experiment_count"],
                summary["score_set_count"],
                summary["mapped_variant_count"],
                datetime.now(UTC).isoformat(),
                round(time.monotonic() - started, 3),
            ),
        )
        con.commit()
    finally:
        con.close()
