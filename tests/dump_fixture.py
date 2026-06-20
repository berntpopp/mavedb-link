"""Build a tiny, valid MaveDB bulk-dump zip for offline mirror tests.

Mirrors the real dump produced by ``mavedb.scripts.export_public_data``:
``main.json`` (nested experimentSets -> experiments -> scoreSets, camelCase) +
``csv/<urn-dashed>.{scores,counts,annotations}.csv`` (the scores/counts CSVs use
the dump's namespaced headers, e.g. ``scores.score``, so tests exercise the
build-time denamespacing back to the live header).
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from tests.fixtures import (
    CALIBRATION_POS,
    EXPERIMENT_SET_URN,
    EXPERIMENT_URN,
    SCORE_SET_RAW,
)

#: A second, calibrated score set (distinct URN) under the same experiment.
CALIBRATED_URN = "urn:mavedb:00001224-a-1"

_CALIBRATED_SCORE_SET: dict[str, Any] = {
    "urn": CALIBRATED_URN,
    "title": "BRCA2 HDR calibrated scan",
    "shortDescription": "HDR functional scores with curated calibrations.",
    "numVariants": 3,
    "license": {"shortName": "CC0", "longName": "CC0 1.0 Universal"},
    "targetGenes": [
        {
            "name": "BRCA2",
            "category": "protein_coding",
            "targetSequence": {"taxonomy": {"organismName": "Homo sapiens"}},
        }
    ],
    "primaryPublicationIdentifiers": [],
    "secondaryPublicationIdentifiers": [],
    "scoreCalibrations": [CALIBRATION_POS],
}

#: scores.csv for the UBE2I set, in the dump's NAMESPACED header form. Note an
#: extra dotted score column (``exp.score``) -> stored header must become
#: ``score,sd,exp.score`` (only the leading ``scores.`` segment is stripped).
_UBE2I_SCORES_NS = (
    "accession,hgvs_nt,hgvs_splice,hgvs_pro,scores.score,scores.sd,scores.exp.score\n"
    "urn:mavedb:00000001-a-1#1,c.1A>T,NA,p.Met1Leu,0.5,0.10,0.4\n"
    "urn:mavedb:00000001-a-1#2,c.2T>G,NA,p.Met1Arg,-1.2,0.20,-1.0\n"
    "urn:mavedb:00000001-a-1#3,c.3G>A,NA,NA,NA,NA,NA\n"
)

#: counts.csv (core columns only for this set).
_UBE2I_COUNTS_NS = (
    "accession,hgvs_nt,hgvs_splice,hgvs_pro\nurn:mavedb:00000001-a-1#1,c.1A>T,NA,p.Met1Leu\n"
)

#: scores.csv for the calibrated set (drives the precomputed distribution).
_CALIBRATED_SCORES_NS = (
    "accession,hgvs_nt,hgvs_splice,hgvs_pro,scores.score\n"
    "urn:mavedb:00001224-a-1#1,c.1A>T,NA,p.Met1Leu,0.94\n"
    "urn:mavedb:00001224-a-1#2,c.2T>G,NA,p.Met1Arg,3.5\n"
    "urn:mavedb:00001224-a-1#3,c.3G>A,NA,p.Gly2Arg,1.0\n"
)

#: annotations.csv (mapped-variant identity layer: VRS digest + ClinGen).
_CALIBRATED_ANNOTATIONS_NS = (
    "accession,hgvs_nt,hgvs_splice,hgvs_pro,mavedb.post_mapped_hgvs_g,"
    "mavedb.post_mapped_vrs_digest,clingen.clingen_allele_id\n"
    "urn:mavedb:00001224-a-1#1,c.1A>T,NA,p.Met1Leu,NC_000013.11:g.32316461A>T,"
    "ga4gh:VA.MINI_digest1,CA999001\n"
    "urn:mavedb:00001224-a-1#2,c.2T>G,NA,p.Met1Arg,NC_000013.11:g.32316462T>G,"
    "ga4gh:VA.MINI_digest2,CA999002\n"
)

#: The dump-wide "as of" timestamp (provenance).
DUMP_AS_OF = "2026-02-06T15:34:44+00:00"


def _csv_name(urn: str, suffix: str) -> str:
    """Dump CSV member name: ``csv/<urn with ':' -> '-'>.<suffix>.csv``."""
    return f"csv/{urn.replace(':', '-')}.{suffix}.csv"


def write_mini_dump(directory: Path) -> Path:
    """Write a minimal but structurally faithful dump zip; return its path."""
    main_json = {
        "title": "MaveDB public data",
        "asOf": DUMP_AS_OF,
        "experimentSets": [
            {
                "urn": EXPERIMENT_SET_URN,
                "title": "UBE2I + BRCA2 experiment set",
                "experiments": [
                    {
                        "urn": EXPERIMENT_URN,
                        "title": "UBE2I complementation",
                        "scoreSets": [SCORE_SET_RAW, _CALIBRATED_SCORE_SET],
                    }
                ],
            }
        ],
    }
    zip_path = directory / "mavedb-dump.mini.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("main.json", json.dumps(main_json))
        zf.writestr("LICENSE.txt", "CC0 1.0 Universal")
        zf.writestr(_csv_name(SCORE_SET_RAW["urn"], "scores"), _UBE2I_SCORES_NS)
        zf.writestr(_csv_name(SCORE_SET_RAW["urn"], "counts"), _UBE2I_COUNTS_NS)
        zf.writestr(_csv_name(CALIBRATED_URN, "scores"), _CALIBRATED_SCORES_NS)
        zf.writestr(_csv_name(CALIBRATED_URN, "annotations"), _CALIBRATED_ANNOTATIONS_NS)
    return zip_path
