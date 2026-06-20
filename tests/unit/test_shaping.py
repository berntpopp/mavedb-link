"""Tests for response-mode shaping of MaveDB entities."""

from __future__ import annotations

import pytest

from mavedb_link.services import shaping
from tests.fixtures import (
    COLLECTION_RAW,
    EXPERIMENT_RAW,
    GENE_RESPONSE,
    MAPPED_VARIANTS_RAW,
    SCORE_SET_RAW,
    SCORE_SET_WITH_CALIBRATIONS_RAW,
    VARIANT_RAW,
)


def test_shape_single_variant_compact_has_score_and_hgvs() -> None:
    out = shaping.shape_single_variant(VARIANT_RAW, "compact")
    assert out["variant_urn"] == "urn:mavedb:00000001-a-1#2"
    assert out["variant_index"] == 2  # F1 join key
    assert out["score_set_urn"] == "urn:mavedb:00000001-a-1"
    assert out["score"] == -1.2
    assert out["hgvs_nt"] == "c.2T>G"
    # compact omits the heavy blocks
    assert "count_data" not in out
    assert "mapped_variants" not in out


def test_shape_single_variant_full_has_blocks() -> None:
    out = shaping.shape_single_variant(VARIANT_RAW, "full")
    assert out["count_data"] == {"c_0": 10, "c_1": 5}
    assert out["mapped_variants"][0]["clingen_allele_id"] == "CA000002"


def test_shape_single_variant_standard_drops_historical_mappings() -> None:
    # F2: embedded mapped_variants are current-only except at full (the old payload
    # leaked superseded current:false rows across several mapping_api_versions).
    from tests.fixtures import VARIANT_RAW_WITH_HISTORY

    standard = shaping.shape_single_variant(VARIANT_RAW_WITH_HISTORY, "standard")
    assert [m["current"] for m in standard["mapped_variants"]] == [True]
    full = shaping.shape_single_variant(VARIANT_RAW_WITH_HISTORY, "full")
    assert {m["current"] for m in full["mapped_variants"]} == {True, False}


@pytest.mark.parametrize("mode", list(shaping.RESPONSE_MODES))
def test_score_set_all_modes_have_identity(mode: str) -> None:
    out = shaping.shape_score_set(SCORE_SET_RAW, mode)
    assert out["urn"] == "urn:mavedb:00000001-a-1"
    assert out["title"]


def test_score_set_minimal_is_anchors_only() -> None:
    out = shaping.shape_score_set(SCORE_SET_RAW, "minimal")
    assert set(out.keys()) == {"urn", "title"}


def test_score_set_compact_drops_empty_and_normalises() -> None:
    out = shaping.shape_score_set(SCORE_SET_RAW, "compact")
    assert out["num_variants"] == 12720
    assert out["license"] == "CC0"
    assert out["experiment_urn"] == "urn:mavedb:00000001-a"
    assert out["targets"][0]["name"] == "UBE2I"
    assert out["targets"][0]["organism"] == "Homo sapiens"
    # compact does not include heavy fields
    assert "method_text" not in out
    assert out["record_url"].endswith("urn:mavedb:00000001-a-1")


def test_score_set_full_includes_heavy_fields() -> None:
    out = shaping.shape_score_set(SCORE_SET_RAW, "full")
    assert out["method_text"]
    assert out["dataset_columns"] == {"scoreColumns": ["score"], "countColumns": []}
    assert out["targets"][0]["external_identifiers"][0]["db_name"] == "Ensembl"
    assert out["publications"]["primary"][0]["title"]
    # full keeps the COMPLETE author list
    assert out["publications"]["primary"][0]["authors"] == [{"name": "Weile J"}]


def test_score_set_standard_caps_authors_and_elides_heavy_text() -> None:
    # F8: standard is the structured record -- capped author list (first author +
    # count, no full list) and no giant free-text blobs (those are full-only).
    out = shaping.shape_score_set(SCORE_SET_RAW, "standard")
    assert out["targets"][0]["external_identifiers"][0]["db_name"] == "Ensembl"
    pub = out["publications"]["primary"][0]
    assert pub["first_author"] == "Weile J"
    assert pub["author_count"] == 1
    assert "authors" not in pub  # full list only at full
    assert "abstract_text" not in out  # heavy free text only at full
    assert "method_text" not in out


def test_score_set_compact_publications_summary() -> None:
    out = shaping.shape_score_set(SCORE_SET_RAW, "compact")
    pubs = out["publications"]
    assert pubs["primary"][0]["identifier"] == "30037627"
    assert pubs["secondary_count"] == 0
    assert "title" not in pubs["primary"][0]  # compact pub omits title


def test_score_set_compact_surfaces_calibrations() -> None:
    out = shaping.shape_score_set(SCORE_SET_WITH_CALIBRATIONS_RAW, "compact")
    calib = out["score_calibrations"][0]
    assert calib["title"] == "IGVF Controls"
    assert calib["baseline_score"] == 5.0
    assert calib["classifications"][0]["acmg"] == "PS3"
    assert calib["classifications"][0]["acmg_strength"] == "STRONG"


def test_score_set_without_calibrations_omits_field() -> None:
    # The base fixture has no scoreCalibrations -> compact drops the empty key.
    out = shaping.shape_score_set(SCORE_SET_RAW, "compact")
    assert "score_calibrations" not in out


def test_score_set_minimal_excludes_calibrations() -> None:
    out = shaping.shape_score_set(SCORE_SET_WITH_CALIBRATIONS_RAW, "minimal")
    assert set(out.keys()) == {"urn", "title"}


def test_score_set_full_calibration_classifications_include_id() -> None:
    out = shaping.shape_score_set(SCORE_SET_WITH_CALIBRATIONS_RAW, "full")
    assert out["score_calibrations"][0]["classifications"][0]["id"] == 249


def test_experiment_shaping() -> None:
    out = shaping.shape_experiment(EXPERIMENT_RAW, "compact")
    assert out["urn"] == "urn:mavedb:00000001-a"
    assert out["score_set_urns"] == ["urn:mavedb:00000001-a-1"]
    assert out["keywords"] == ["Endogenous locus library method"]


def test_gene_shaping() -> None:
    out = shaping.shape_gene(GENE_RESPONSE, "compact")
    assert out["symbol"] == "UBE2I"
    assert out["hgnc_id"] == "HGNC:12485"


def test_mapped_variant_shaping() -> None:
    out = shaping.shape_mapped_variant(MAPPED_VARIANTS_RAW[0], "compact")
    assert out["variant_urn"] == "urn:mavedb:00000001-a-1#1"
    assert out["vrs_id"] == "ga4gh:VA.KJ_post1"
    assert out["clingen_allele_id"] == "CA000001"
    assert "post_mapped" not in out  # heavy field only in standard/full
    full = shaping.shape_mapped_variant(MAPPED_VARIANTS_RAW[0], "full")
    assert full["post_mapped"]["id"] == "ga4gh:VA.KJ_post1"


def test_collection_shaping() -> None:
    out = shaping.shape_collection(COLLECTION_RAW, "compact", limit=100, offset=0)
    assert out["name"] == "UBE2I datasets"
    assert out["score_set_urns"] == ["urn:mavedb:00000001-a-1"]
    assert out["num_score_sets"] == 1  # F12 total
    assert out["truncated"] is False
    minimal = shaping.shape_collection(COLLECTION_RAW, "minimal", limit=100, offset=0)
    assert set(minimal.keys()) == {"urn", "name"}
