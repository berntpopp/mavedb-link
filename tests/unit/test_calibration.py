"""Tests for the pure calibration classifier + shaper (data-plane, no I/O)."""

from __future__ import annotations

from mavedb_link.services.calibration import (
    classify_score,
    primary_classification,
    shape_calibrations,
)
from tests.fixtures import CALIBRATION_NEG_GAPPED, CALIBRATION_POS

# --- classify_score: the score -> functional-class mapping ----------------------


def test_classify_abnormal_carries_acmg_and_oddspath() -> None:
    out = classify_score(0.94, [CALIBRATION_POS])
    assert len(out) == 1
    entry = out[0]
    assert entry["calibration"] == "IGVF Controls"
    assert entry["classification"] == "abnormal"
    assert entry["label"] == "Functionally abnormal"
    assert entry["acmg"] == "PS3"
    assert entry["acmg_strength"] == "STRONG"
    assert entry["oddspath"] == 42.2
    assert entry["baseline_score"] == 5.0


def test_classify_normal() -> None:
    entry = classify_score(3.5, [CALIBRATION_POS])[0]
    assert entry["classification"] == "normal"
    assert entry["acmg"] == "BS3"


def test_classify_intermediate_is_not_specified() -> None:
    entry = classify_score(2.0, [CALIBRATION_POS])[0]
    assert entry["classification"] == "not_specified"
    assert entry["label"] == "Intermediate"
    assert "acmg" not in entry  # intermediate bin has no ACMG criterion


def test_classify_gap_is_indeterminate() -> None:
    # -0.7 lands between the benign (> -0.58) and pathogenic (< -0.90) bins.
    entry = classify_score(-0.7, [CALIBRATION_NEG_GAPPED])[0]
    assert entry["classification"] == "indeterminate"
    assert "acmg" not in entry


def test_classify_is_direction_agnostic() -> None:
    # Same negative-direction calibration: low score = abnormal, high = normal.
    assert classify_score(-1.5, [CALIBRATION_NEG_GAPPED])[0]["classification"] == "abnormal"
    assert classify_score(-0.3, [CALIBRATION_NEG_GAPPED])[0]["classification"] == "normal"


def test_classify_returns_one_entry_per_calibration() -> None:
    out = classify_score(0.94, [CALIBRATION_POS, CALIBRATION_NEG_GAPPED])
    assert [e["calibration"] for e in out] == ["IGVF Controls", "ExCALIBR calibration"]
    # 0.94 is abnormal under POS but normal (>-0.58) under the negative calibration.
    assert out[0]["classification"] == "abnormal"
    assert out[1]["classification"] == "normal"


def test_classify_no_calibrations_is_empty() -> None:
    assert classify_score(0.94, []) == []
    assert classify_score(0.94, None) == []


def test_classify_none_score_is_empty() -> None:
    assert classify_score(None, [CALIBRATION_POS]) == []


def test_classify_inclusive_upper_bound_includes_boundary() -> None:
    calib = {
        "title": "inc",
        "functionalClassifications": [
            {
                "label": "x",
                "functionalClassification": "abnormal",
                "range": [None, 1.0],
                "inclusiveLowerBound": False,
                "inclusiveUpperBound": True,
                "id": 1,
            }
        ],
    }
    assert classify_score(1.0, [calib])[0]["classification"] == "abnormal"  # inclusive
    assert classify_score(1.01, [calib])[0]["classification"] == "indeterminate"  # outside


# --- shape_calibrations: camelCase -> tidy snake_case --------------------------


def test_shape_calibrations_normalizes_fields() -> None:
    out = shape_calibrations([CALIBRATION_POS], full=False)
    assert len(out) == 1
    calib = out[0]
    assert calib["title"] == "IGVF Controls"
    assert calib["baseline_score"] == 5.0
    cls = calib["classifications"][0]
    assert cls["classification"] == "abnormal"
    assert cls["range"] == [None, 1.49]
    assert cls["acmg"] == "PS3"
    assert cls["acmg_strength"] == "STRONG"
    assert cls["oddspath"] == 42.2
    assert cls["variant_count"] == 137


def test_shape_calibrations_threshold_sources_are_compact() -> None:
    src = shape_calibrations([CALIBRATION_POS], full=False)[0]["threshold_sources"][0]
    assert src == {
        "db_name": "PubMed",
        "identifier": "38417439",
        "title": "Functional analysis of 462 germline BRCA2 missense variants.",
    }
    assert "authors" not in src  # heavy author list dropped


def test_shape_calibrations_empty() -> None:
    assert shape_calibrations([], full=False) == []
    assert shape_calibrations(None, full=False) == []


# --- primary_classification: single verdict for per-row tagging ----------------


def test_primary_classification_prefers_the_primary_calibration() -> None:
    # Negative calibration listed first but POS is flagged primary; for score 0.94
    # POS says abnormal while the negative one says normal -> primary wins.
    verdict = primary_classification(0.94, [CALIBRATION_NEG_GAPPED, CALIBRATION_POS])
    assert verdict == "abnormal"


def test_primary_classification_none_without_calibrations() -> None:
    assert primary_classification(0.94, []) is None
    assert primary_classification(None, [CALIBRATION_POS]) is None
