"""Tests for the scores-CSV parser and pagination shaping."""

from __future__ import annotations

from mavedb_link.services.scores import parse_scores_csv, shape_scores
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
