"""Read-only repository over the local SQLite mirror (data plane).

Returns upstream-shaped records (camelCase) and CSV pages so the P3 client shim
can serve them transparently; returns None/[] on a mirror-miss (never raises for
absence — the shim falls through to the live API on None).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mavedb_link.data.repository import MirrorRepository
from mavedb_link.ingest.builder import build_database
from tests.dump_fixture import CALIBRATED_URN, DUMP_AS_OF, write_mini_dump
from tests.fixtures import EXPERIMENT_SET_URN, EXPERIMENT_URN, SCORE_SET_URN


@pytest.fixture
def repo(tmp_path: Path) -> MirrorRepository:
    db_path = tmp_path / "mavedb.sqlite"
    build_database(write_mini_dump(tmp_path), db_path, zenodo_record="18511521")
    r = MirrorRepository.open(db_path)
    assert r is not None
    yield r
    r.close()


def test_open_returns_none_for_missing_db(tmp_path: Path) -> None:
    assert MirrorRepository.open(tmp_path / "nope.sqlite") is None


def test_meta_carries_provenance(repo: MirrorRepository) -> None:
    meta = repo.meta()
    assert meta["dump_as_of"] == DUMP_AS_OF
    assert meta["zenodo_record"] == "18511521"
    assert meta["score_set_count"] == 2


def test_score_set_record_is_upstream_shaped(repo: MirrorRepository) -> None:
    rec = repo.score_set_record(SCORE_SET_URN)
    assert rec is not None
    assert rec["numVariants"] == 12720
    assert rec["experiment"]["urn"] == EXPERIMENT_URN
    assert repo.score_set_record("urn:mavedb:99999999-z-9") is None
    assert repo.has_score_set(SCORE_SET_URN) is True
    assert repo.has_score_set("urn:mavedb:99999999-z-9") is False


def test_experiment_and_set_records(repo: MirrorRepository) -> None:
    exp = repo.experiment_record(EXPERIMENT_URN)
    assert exp is not None and exp["experimentSetUrn"] == EXPERIMENT_SET_URN
    es = repo.experiment_set_record(EXPERIMENT_SET_URN)
    assert es is not None and es["urn"] == EXPERIMENT_SET_URN
    assert repo.experiment_record("urn:mavedb:99999999-z") is None


def test_scores_csv_paging(repo: MirrorRepository) -> None:
    full = repo.scores_csv(SCORE_SET_URN, start=0, limit=10_000)
    assert full is not None
    lines = full.strip().splitlines()
    assert lines[0] == "accession,hgvs_nt,hgvs_splice,hgvs_pro,score,sd,exp.score"
    assert len(lines) == 4  # header + 3 rows
    page = repo.scores_csv(SCORE_SET_URN, start=1, limit=1)
    pl = page.strip().splitlines()
    assert pl[0] == lines[0]  # header always present
    assert len(pl) == 2  # header + 1 row
    assert pl[1].startswith("urn:mavedb:00000001-a-1#2")
    assert repo.scores_csv("urn:mavedb:99999999-z-9", start=0, limit=10) is None


def test_counts_csv_present(repo: MirrorRepository) -> None:
    counts = repo.counts_csv(SCORE_SET_URN, start=0, limit=10)
    assert counts is not None and counts.splitlines()[0] == "accession,hgvs_nt,hgvs_splice,hgvs_pro"


def test_distribution_lookup(repo: MirrorRepository) -> None:
    dist = repo.distribution(CALIBRATED_URN)
    assert dist is not None
    assert dist["n"] == 3
    assert dist["quantiles"]["p50"] == 1.0
    assert len(dist["histogram"]) == 10
    assert repo.distribution(SCORE_SET_URN) is not None  # UBE2I also has scores
    assert repo.distribution("urn:mavedb:99999999-z-9") is None


def test_find_mapped_variant_cross_keys(repo: MirrorRepository) -> None:
    by_vrs = repo.mapped_by_vrs("ga4gh:VA.MINI_digest1")
    assert by_vrs and by_vrs[0]["variant_urn"] == f"{CALIBRATED_URN}#1"
    by_clingen = repo.mapped_by_clingen("CA999002")
    assert by_clingen and by_clingen[0]["score_set_urn"] == CALIBRATED_URN
    by_urn = repo.mapped_by_variant_urn(f"{CALIBRATED_URN}#2")
    assert by_urn and by_urn[0]["vrs_id"] == "ga4gh:VA.MINI_digest2"
    assert repo.mapped_by_vrs("ga4gh:VA.absent") == []


def test_gene_and_search(repo: MirrorRepository) -> None:
    ube2i = repo.gene_score_sets("UBE2I")
    assert [r["urn"] for r in ube2i] == [SCORE_SET_URN]
    # case-insensitive
    assert repo.gene_score_set_urns("ube2i") == [SCORE_SET_URN]
    hits = repo.search_score_sets("BRCA2")
    assert [r["urn"] for r in hits] == [CALIBRATED_URN]
    by_target = repo.search_score_sets(None, targets=["UBE2I"])
    assert [r["urn"] for r in by_target] == [SCORE_SET_URN]
