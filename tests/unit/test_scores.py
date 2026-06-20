"""Tests for the scores-CSV parser and pagination shaping."""

from __future__ import annotations

from mavedb_link.services.scores import hgvs_matches, parse_scores_csv, shape_scores
from tests.fixtures import SCORES_CSV


def test_parse_columns_and_rows() -> None:
    columns, rows = parse_scores_csv(SCORES_CSV)
    assert columns == ["accession", "hgvs_nt", "hgvs_splice", "hgvs_pro", "score", "sd"]
    assert len(rows) == 3


def test_numeric_coercion_and_na() -> None:
    _columns, rows = parse_scores_csv(SCORES_CSV)
    assert rows[0]["score"] == 0.5
    assert rows[0]["sd"] == 0.1
    assert rows[1]["score"] == -1.2
    # hgvs columns stay strings even though they contain digits / 'NA'
    assert rows[0]["hgvs_nt"] == "c.1A>T"
    assert rows[0]["hgvs_splice"] is None  # 'NA' -> None
    # fully-NA row: numeric + hgvs both None
    assert rows[2]["score"] is None
    assert rows[2]["hgvs_pro"] is None


def test_empty_csv() -> None:
    assert parse_scores_csv("") == ([], [])


def test_shape_scores_pagination_heuristic() -> None:
    payload = shape_scores(SCORES_CSV, start=0, limit=3)
    assert payload["returned"] == 3
    assert payload["start"] == 0
    assert payload["limit"] == 3
    # returned == limit -> may be more
    assert payload["truncated"] is True
    assert payload["next_start"] == 3


def test_shape_scores_with_known_total() -> None:
    payload = shape_scores(SCORES_CSV, start=0, limit=10, num_variants=3)
    assert payload["total"] == 3
    assert payload["truncated"] is False
    assert payload["next_start"] is None


def test_shape_scores_partial_page_not_truncated() -> None:
    payload = shape_scores(SCORES_CSV, start=0, limit=100)
    assert payload["returned"] == 3
    assert payload["truncated"] is False


def test_shape_scores_minimal_drops_hgvs_columns() -> None:
    # F7b: a token-safe lean mode -> {accession, variant_index, score} only.
    payload = shape_scores(SCORES_CSV, start=0, limit=3, response_mode="minimal")
    row = payload["rows"][0]
    assert set(row) <= {"accession", "variant_index", "score"}
    assert "hgvs_nt" not in row
    assert payload["columns"] == ["accession", "variant_index", "score"]


def test_shape_scores_compact_keeps_hgvs_columns() -> None:
    payload = shape_scores(SCORES_CSV, start=0, limit=3, response_mode="compact")
    assert "hgvs_nt" in payload["rows"][0]
    assert "hgvs_nt" in payload["columns"]


# --- F5: accession-prefix-insensitive hgvs matching ---------------------------
# Live-verified: BRCA2 SGE sets store hgvs_nt accession-prefixed
# (ENST00000380152.8:c.8168A>G) so a bare 'c.8168A>G' used to 404.

_PREFIXED_ROW = {"hgvs_nt": "ENST00000380152.8:c.8168A>G", "hgvs_pro": None, "accession": "u#1"}
_BARE_ROW = {"hgvs_nt": "c.2T>G", "hgvs_pro": "p.Met1Arg", "accession": "u#2"}


def test_hgvs_matches_bare_query_against_prefixed_row() -> None:
    # the high-value win: a bare c. form resolves the accession-prefixed stored value
    assert hgvs_matches(_PREFIXED_ROW, "c.8168a>g")


def test_hgvs_matches_prefixed_query_against_bare_row() -> None:
    # symmetric: a fully-prefixed query resolves a bare stored value
    assert hgvs_matches(_BARE_ROW, "enst00000380152.8:c.2t>g")


def test_hgvs_matches_full_prefixed_equality_still_works() -> None:
    assert hgvs_matches(_PREFIXED_ROW, "enst00000380152.8:c.8168a>g")


def test_hgvs_matches_does_not_false_positive_on_accession() -> None:
    # a bare c. query must NOT spuriously match the accession (variant URN) column
    assert not hgvs_matches({"accession": "urn:mavedb:00000001-a-1#2"}, "c.8168a>g")


def test_hgvs_matches_distinct_variants_do_not_match() -> None:
    assert not hgvs_matches(_PREFIXED_ROW, "c.9999g>a")
