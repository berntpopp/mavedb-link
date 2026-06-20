"""standard trims VRS scaffolding to a flat genomic summary; full keeps everything."""

from __future__ import annotations

from mavedb_link.services.shaping import shape_mapped_variant

_RAW = {
    "variantUrn": "urn:mavedb:1-a-1#1",
    "clingenAlleleId": "CA123",
    "current": True,
    "vrsVersion": "2.0",
    "alignmentLevel": "chromosome",
    "preMapped": {"id": "ga4gh:VA.pre", "location": {"start": 1, "end": 2}},
    "postMapped": {
        "id": "ga4gh:VA.post",
        "location": {
            "sequenceReference": {"refgetAccession": "SQ.abc", "assembly": "GRCh38"},
            "start": 43044294,
            "end": 43044295,
        },
        "state": {"sequence": "T"},
    },
}


def test_standard_drops_pre_mapped_and_flattens_post() -> None:
    out = shape_mapped_variant(_RAW, "standard")
    assert "pre_mapped" not in out
    assert out["vrs_id"] == "ga4gh:VA.post"
    pm = out["post_mapped"]
    assert pm["sequence_id"] == "SQ.abc"
    assert pm["assembly"] == "GRCh38"
    assert pm["start"] == 43044294
    assert pm["end"] == 43044295
    assert pm["alt"] == "T"
    assert out["vrs_version"] == "2.0"
    assert out["alignment_level"] == "chromosome"


def test_full_keeps_full_objects() -> None:
    out = shape_mapped_variant(_RAW, "full")
    assert out["pre_mapped"] == _RAW["preMapped"]
    assert out["post_mapped"] == _RAW["postMapped"]  # untouched nested object


def test_compact_identity_only() -> None:
    out = shape_mapped_variant(_RAW, "compact")
    assert "post_mapped" not in out and "pre_mapped" not in out
    assert out["vrs_id"] == "ga4gh:VA.post"
