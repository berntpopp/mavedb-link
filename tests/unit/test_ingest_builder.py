"""Builder contract: a dump zip -> a queryable read-only SQLite mirror.

The builder streams the Zenodo bulk dump (main.json + per-set CSVs) into SQLite:
camelCase records land verbatim (so the existing shapers consume them), scores
CSVs are denamespaced back to the live header, per-set distribution summaries are
precomputed, and the annotations CSV becomes a cross-dataset mapped-variant index.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from mavedb_link.ingest.builder import build_database
from tests.dump_fixture import CALIBRATED_URN, DUMP_AS_OF, write_mini_dump
from tests.fixtures import EXPERIMENT_SET_URN, EXPERIMENT_URN, SCORE_SET_URN


def _build(tmp_path: Path) -> tuple[Path, dict]:
    dump = write_mini_dump(tmp_path)
    db_path = tmp_path / "mavedb.sqlite"
    summary = build_database(dump, db_path, source_md5="deadbeef", zenodo_record="18511521")
    assert db_path.exists()
    return db_path, summary


def test_build_summary_and_meta(tmp_path: Path) -> None:
    db_path, summary = _build(tmp_path)
    assert summary["score_set_count"] == 2
    assert summary["experiment_count"] == 1
    assert summary["experiment_set_count"] == 1
    assert summary["mapped_variant_count"] == 2
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT dump_as_of, score_set_count, source_md5, zenodo_record, schema_version "
            "FROM meta WHERE id = 1"
        ).fetchone()
    finally:
        con.close()
    assert row[0] == DUMP_AS_OF
    assert row[1] == 2
    assert row[2] == "deadbeef"
    assert row[3] == "18511521"
    assert isinstance(row[4], int) and row[4] >= 1


def test_score_set_record_is_camelcase_and_parent_enriched(tmp_path: Path) -> None:
    db_path, _ = _build(tmp_path)
    con = sqlite3.connect(db_path)
    try:
        rec, exp_urn, has_cal = con.execute(
            "SELECT record_json, experiment_urn, has_calibrations FROM score_set WHERE urn = ?",
            (SCORE_SET_URN,),
        ).fetchone()
        cal_row = con.execute(
            "SELECT has_calibrations FROM score_set WHERE urn = ?", (CALIBRATED_URN,)
        ).fetchone()
    finally:
        con.close()
    record = json.loads(rec)
    # camelCase survives verbatim (the shapers read these keys)...
    assert record["numVariants"] == 12720
    assert record["license"]["shortName"] == "CC0"
    # ...and the nested score set is enriched with its parent experiment URN.
    assert record["experiment"]["urn"] == EXPERIMENT_URN
    assert exp_urn == EXPERIMENT_URN
    assert has_cal == 0
    assert cal_row[0] == 1


def test_experiment_records_carry_parents(tmp_path: Path) -> None:
    db_path, _ = _build(tmp_path)
    con = sqlite3.connect(db_path)
    try:
        es = con.execute("SELECT urn FROM experiment_set").fetchall()
        exp = con.execute(
            "SELECT experiment_set_urn FROM experiment WHERE urn = ?", (EXPERIMENT_URN,)
        ).fetchone()
    finally:
        con.close()
    assert [r[0] for r in es] == [EXPERIMENT_SET_URN]
    assert exp[0] == EXPERIMENT_SET_URN


def test_scores_csv_is_denamespaced_to_live_header(tmp_path: Path) -> None:
    db_path, _ = _build(tmp_path)
    con = sqlite3.connect(db_path)
    try:
        scores_csv, counts_csv = con.execute(
            "SELECT scores_csv, counts_csv FROM score_set_data WHERE urn = ?", (SCORE_SET_URN,)
        ).fetchone()
    finally:
        con.close()
    header = scores_csv.splitlines()[0]
    # Leading "scores." stripped; dotted score column body preserved.
    assert header == "accession,hgvs_nt,hgvs_splice,hgvs_pro,score,sd,exp.score"
    assert "scores.score" not in scores_csv
    assert counts_csv.splitlines()[0] == "accession,hgvs_nt,hgvs_splice,hgvs_pro"
    # Data rows survive intact.
    assert "urn:mavedb:00000001-a-1#2,c.2T>G,NA,p.Met1Arg,-1.2,0.20,-1.0" in scores_csv


def test_distribution_is_precomputed(tmp_path: Path) -> None:
    db_path, _ = _build(tmp_path)
    con = sqlite3.connect(db_path)
    try:
        n, lo, hi, hist_json, q_json = con.execute(
            "SELECT n, min, max, histogram_json, quantiles_json "
            "FROM score_distribution WHERE score_set_urn = ?",
            (CALIBRATED_URN,),
        ).fetchone()
    finally:
        con.close()
    assert n == 3
    assert lo == 0.94
    assert hi == 3.5
    histogram = json.loads(hist_json)
    assert isinstance(histogram, list) and len(histogram) == 10
    assert sum(b["count"] for b in histogram) == 3
    quantiles = json.loads(q_json)
    assert quantiles["p50"] == 1.0  # median of {0.94, 1.0, 3.5}


def test_mapped_variant_index_from_annotations(tmp_path: Path) -> None:
    db_path, _ = _build(tmp_path)
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT variant_urn, vrs_id, clingen_allele_id FROM mapped_variant "
            "WHERE score_set_urn = ? ORDER BY variant_urn",
            (CALIBRATED_URN,),
        ).fetchall()
        by_clingen = con.execute(
            "SELECT score_set_urn FROM mapped_variant WHERE clingen_allele_id = ?", ("CA999002",)
        ).fetchone()
    finally:
        con.close()
    assert rows[0] == (f"{CALIBRATED_URN}#1", "ga4gh:VA.MINI_digest1", "CA999001")
    assert by_clingen[0] == CALIBRATED_URN


def test_gene_index_maps_symbols_to_score_sets(tmp_path: Path) -> None:
    db_path, _ = _build(tmp_path)
    con = sqlite3.connect(db_path)
    try:
        ube2i = con.execute(
            "SELECT score_set_urn FROM gene_index WHERE gene_symbol_upper = ?", ("UBE2I",)
        ).fetchall()
        brca2 = con.execute(
            "SELECT score_set_urn FROM gene_index WHERE gene_symbol_upper = ?", ("BRCA2",)
        ).fetchall()
    finally:
        con.close()
    assert [r[0] for r in ube2i] == [SCORE_SET_URN]
    assert [r[0] for r in brca2] == [CALIBRATED_URN]


def test_build_is_atomic_replace(tmp_path: Path) -> None:
    # Re-building over an existing DB swaps cleanly (no .tmp left behind).
    _build(tmp_path)
    _db_path, summary = _build(tmp_path)
    assert summary["score_set_count"] == 2
    leftovers = list(tmp_path.glob("*.tmp")) + list(tmp_path.glob("*.sqlite-*"))
    assert not leftovers
