"""hgvs_index is populated from the scores CSV during the mirror build (schema v2)."""

from __future__ import annotations

from pathlib import Path

from mavedb_link.constants import MIRROR_SCHEMA_VERSION
from mavedb_link.data.repository import MirrorRepository
from mavedb_link.ingest.builder import build_database
from mavedb_link.ingest.parsing import extract_hgvs_rows
from tests.dump_fixture import write_mini_dump


def test_schema_version_is_two() -> None:
    assert MIRROR_SCHEMA_VERSION == 2


def test_extract_hgvs_rows_normalizes_and_scopes() -> None:
    csv = (
        "accession,hgvs_nt,hgvs_pro,hgvs_splice,score\n"
        "urn:mavedb:00000001-a-1#1,ENST00000380152.8:c.8168A>G,p.Asp2723His,NA,1.2\n"
        "urn:mavedb:00000001-a-1#2,NA,NA,NA,0.4\n"  # no hgvs -> dropped
    )
    rows = extract_hgvs_rows(csv, "urn:mavedb:00000001-a-1")
    assert rows == [
        {
            "score_set_urn": "urn:mavedb:00000001-a-1",
            "variant_urn": "urn:mavedb:00000001-a-1#1",
            "hgvs_nt": "c.8168a>g",  # prefix-stripped + lowercased
            "hgvs_pro": "p.asp2723his",
            "hgvs_splice": None,
        }
    ]


def test_build_populates_hgvs_index(tmp_path: Path) -> None:
    db = tmp_path / "mavedb.sqlite"
    build_database(write_mini_dump(tmp_path), db, zenodo_record="18511521")
    repo = MirrorRepository.open(db)
    assert repo is not None
    # The calibrated (BRCA2) set has hgvs_pro p.Met1Leu mapped to a VRS digest.
    rows = repo.resolve_hgvs("p.met1leu", gene="BRCA2")
    assert any(r["vrs_id"] == "ga4gh:VA.MINI_digest1" for r in rows)
    repo.close()
