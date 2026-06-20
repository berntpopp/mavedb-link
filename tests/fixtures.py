"""Canned MaveDB API payloads (camelCase, as the upstream returns them).

Modelled on real records (e.g. urn:mavedb:00000001-a-1, UBE2I) but trimmed to the
fields the shapers read, so unit tests are deterministic and network-free.
"""

from __future__ import annotations

from typing import Any

BASE_URL = "https://api.test.mavedb/api/v1"

SCORE_SET_URN = "urn:mavedb:00000001-a-1"
EXPERIMENT_URN = "urn:mavedb:00000001-a"
EXPERIMENT_SET_URN = "urn:mavedb:00000001"

SCORE_SET_RAW: dict[str, Any] = {
    "urn": SCORE_SET_URN,
    "title": "UBE2I deep mutational scan",
    "shortDescription": "Saturation DMS of UBE2I complementation in yeast.",
    "abstractText": "A multiplexed assay of UBE2I variant effect.",
    "methodText": "Variants scored by yeast complementation.",
    "numVariants": 12720,
    "license": {"shortName": "CC0", "longName": "CC0 1.0 Universal", "link": "https://x"},
    "targetGenes": [
        {
            "name": "UBE2I",
            "category": "protein_coding",
            "externalIdentifiers": [
                {"identifier": {"dbName": "Ensembl", "identifier": "ENSG00000103275"}, "offset": 0},
                {"identifier": {"dbName": "RefSeq", "identifier": "NM_003345"}, "offset": 0},
                {"identifier": {"dbName": "UniProt", "identifier": "P63279"}, "offset": 0},
            ],
            "targetSequence": {
                "sequenceType": "dna",
                "sequence": "ATGTCG",
                "taxonomy": {"organismName": "Homo sapiens", "taxId": 9606, "code": 9606},
            },
        }
    ],
    "experiment": {"urn": EXPERIMENT_URN},
    "primaryPublicationIdentifiers": [
        {
            "identifier": "30037627",
            "dbName": "PubMed",
            "title": "Deep mutational scanning of UBE2I",
            "doi": "10.1016/j.cels.2018.05.011",
            "publicationYear": 2018,
            "publicationJournal": "Cell Systems",
            "url": "https://pubmed.ncbi.nlm.nih.gov/30037627",
            "authors": [{"name": "Weile J"}],
        }
    ],
    "secondaryPublicationIdentifiers": [],
    "doiIdentifiers": [{"identifier": "10.5281/zenodo.0000000"}],
    "datasetColumns": {"scoreColumns": ["score"], "countColumns": []},
    "metaAnalyzesScoreSetUrns": [],
    "metaAnalyzedByScoreSetUrns": [],
    "supersededScoreSet": None,
    "supersedingScoreSet": None,
    "processingState": "success",
    "mappingState": "complete",
    "private": False,
    "publishedDate": "2019-08-08",
    "creationDate": "2019-01-01",
    "modificationDate": "2019-08-08",
    "officialCollections": [],
    "externalLinks": {},
}

SCORE_SETS_SEARCH_RESPONSE: dict[str, Any] = {
    "scoreSets": [SCORE_SET_RAW],
    "numScoreSets": 1,
}

#: A second score set surfaced ONLY by the target-name search (not the gene
#: endpoint) — drives the DEF-1 union/dedupe differential.
SCORE_SET_URN_2 = "urn:mavedb:00000002-a-1"
SCORE_SET_RAW_2: dict[str, Any] = {
    "urn": SCORE_SET_URN_2,
    "title": "UBE2I RING domain scan",
    "numVariants": 100,
    "license": {"shortName": "CC BY 4.0"},
    "targetGenes": [
        {
            "name": "UBE2I",
            "category": "protein_coding",
            "targetSequence": {"taxonomy": {"organismName": "Homo sapiens"}},
        }
    ],
    "primaryPublicationIdentifiers": [],
    "secondaryPublicationIdentifiers": [],
}
GENE_TARGET_SEARCH_RESPONSE: dict[str, Any] = {
    "scoreSets": [SCORE_SET_RAW_2],
    "numScoreSets": 1,
}

GENE_RESPONSE: dict[str, Any] = {
    "symbol": "UBE2I",
    "name": "ubiquitin conjugating enzyme E2 I",
    "hgncId": "HGNC:12485",
    "locusGroup": "protein-coding gene",
    "location": "16p13.3",
    "omimId": "601661",
    "ensemblGeneId": "ENSG00000103275",
    "scoreSets": [SCORE_SET_RAW],
    "total": 1,
    "totalScoredVariants": 12720,
    "limit": 20,
    "offset": 0,
}

EXPERIMENT_RAW: dict[str, Any] = {
    "urn": EXPERIMENT_URN,
    "title": "UBE2I complementation",
    "shortDescription": "Yeast complementation assay for UBE2I.",
    "abstractText": "Experiment abstract.",
    "methodText": "Experiment methods.",
    "experimentSetUrn": EXPERIMENT_SET_URN,
    "scoreSetUrns": [SCORE_SET_URN],
    "numScoreSets": 1,
    "keywords": [{"keyword": {"label": "Endogenous locus library method"}}],
    "primaryPublicationIdentifiers": SCORE_SET_RAW["primaryPublicationIdentifiers"],
    "secondaryPublicationIdentifiers": [],
    "doiIdentifiers": [],
    "publishedDate": "2019-08-08",
    "creationDate": "2019-01-01",
    "processingState": "success",
}

EXPERIMENTS_SEARCH_RESPONSE: dict[str, Any] = {
    "experiments": [EXPERIMENT_RAW],
    "numExperiments": 1,
}

MAPPED_VARIANTS_RAW: list[dict[str, Any]] = [
    {
        "variantUrn": f"{SCORE_SET_URN}#1",
        "preMapped": {"type": "Allele", "id": "ga4gh:VA.pre1"},
        "postMapped": {"type": "Allele", "id": "ga4gh:VA.KJ_post1"},
        "clingenAlleleId": "CA000001",
        "current": True,
        "vrsVersion": "2.0",
        "mappingApiVersion": "1.0",
        "alignmentLevel": "exact",
    },
    {
        "variantUrn": f"{SCORE_SET_URN}#2",
        "preMapped": {"type": "Allele", "id": "ga4gh:VA.pre2"},
        "postMapped": {"type": "Allele", "id": "ga4gh:VA.KJ_post2"},
        "clingenAlleleId": "CA000002",
        "current": True,
    },
]

COLLECTION_URN = "abcdEFGH"
COLLECTION_RAW: dict[str, Any] = {
    "urn": COLLECTION_URN,
    "name": "UBE2I datasets",
    "description": "Curated UBE2I MAVE datasets.",
    "badgeName": None,
    "experimentUrns": [EXPERIMENT_URN],
    "scoreSetUrns": [SCORE_SET_URN],
    "private": False,
}

API_VERSION_RESPONSE: dict[str, Any] = {"name": "mavedb-api", "version": "2026.2.4"}

#: A single-variant record as GET /variants/{variant_urn} returns it (DEF-6).
VARIANT_URN = f"{SCORE_SET_URN}#2"
#: The '#' is percent-encoded in the request path (else httpx drops it as a fragment).
VARIANT_URN_ENCODED = VARIANT_URN.replace("#", "%23")
VARIANT_RAW: dict[str, Any] = {
    "urn": VARIANT_URN,
    "hgvsNt": "c.2T>G",
    "hgvsPro": "p.Met1Arg",
    "scoreSet": {"urn": SCORE_SET_URN},
    "data": {
        "score_data": {"score": -1.2, "sd": 0.2},
        "count_data": {"c_0": 10, "c_1": 5},
    },
    "mappedVariants": [
        {
            "variantUrn": VARIANT_URN,
            "postMapped": {"type": "Allele", "id": "ga4gh:VA.KJ_post2"},
            "clingenAlleleId": "CA000002",
            "current": True,
        }
    ],
}

SCORES_CSV = (
    "accession,hgvs_nt,hgvs_splice,hgvs_pro,score,sd\n"
    "urn:mavedb:00000001-a-1#1,c.1A>T,NA,p.Met1Leu,0.5,0.10\n"
    "urn:mavedb:00000001-a-1#2,c.2T>G,NA,p.Met1Arg,-1.2,0.20\n"
    "urn:mavedb:00000001-a-1#3,c.3G>A,NA,NA,NA,NA\n"
)

#: A positive-direction calibration (higher = normal; WT anchor = 5), modelled on
#: the BRCA2 HDR IGVF controls (urn:mavedb:00001224-a-1). Bins are both-exclusive.
CALIBRATION_POS: dict[str, Any] = {
    "title": "IGVF Controls",
    "researchUseOnly": False,
    "baselineScore": 5.0,
    "baselineScoreDescription": "Wild-type HDR activity.",
    "primary": True,
    "functionalClassifications": [
        {
            "label": "Functionally abnormal",
            "functionalClassification": "abnormal",
            "range": [None, 1.49],
            "inclusiveLowerBound": False,
            "inclusiveUpperBound": False,
            "acmgClassification": {"criterion": "PS3", "evidenceStrength": "STRONG"},
            "oddspathsRatio": 42.2,
            "id": 249,
            "variantCount": 137,
        },
        {
            "label": "Intermediate",
            "functionalClassification": "not_specified",
            "range": [1.49, 2.5],
            "inclusiveLowerBound": False,
            "inclusiveUpperBound": False,
            "id": 251,
            "variantCount": 12,
        },
        {
            "label": "Functionally normal",
            "functionalClassification": "normal",
            "range": [2.5, None],
            "inclusiveLowerBound": False,
            "inclusiveUpperBound": False,
            "acmgClassification": {"criterion": "BS3", "evidenceStrength": "STRONG"},
            "oddspathsRatio": 0.02,
            "id": 250,
            "variantCount": 313,
        },
    ],
    "thresholdSources": [
        {
            "identifier": "38417439",
            "dbName": "PubMed",
            "title": "Functional analysis of 462 germline BRCA2 missense variants.",
            "authors": [{"name": "Hu C", "primary": True}],
        }
    ],
}

#: A negative-direction, GAPPED, null-baseline calibration (ExCALIBR-like): scores
#: in (-0.90, -0.58) fall in no bin -> indeterminate. Higher score = normal.
CALIBRATION_NEG_GAPPED: dict[str, Any] = {
    "title": "ExCALIBR calibration",
    "researchUseOnly": False,
    "baselineScore": None,
    "primary": False,
    "functionalClassifications": [
        {
            "label": "BS3 Supporting (-1)",
            "functionalClassification": "normal",
            "range": [-0.58, None],
            "inclusiveLowerBound": False,
            "inclusiveUpperBound": False,
            "acmgClassification": {"criterion": "BS3", "evidenceStrength": "SUPPORTING"},
            "id": 11,
            "variantCount": 308,
        },
        {
            "label": "PS3 Supporting (1)",
            "functionalClassification": "abnormal",
            "range": [None, -0.90],
            "inclusiveLowerBound": False,
            "inclusiveUpperBound": False,
            "acmgClassification": {"criterion": "PS3", "evidenceStrength": "SUPPORTING"},
            "id": 12,
            "variantCount": 102,
        },
    ],
    "thresholdSources": [],
}

#: Score-set record that carries calibrations (the decision-relevant case).
SCORE_SET_WITH_CALIBRATIONS_RAW: dict[str, Any] = {
    **SCORE_SET_RAW,
    "scoreCalibrations": [CALIBRATION_POS, CALIBRATION_NEG_GAPPED],
}

#: A 10-row scores CSV (scores 0.0..9.0) for distribution-summary tests.
DISTRIBUTION_SCORES_CSV = "accession,hgvs_nt,score\n" + "".join(
    f"urn:mavedb:00000001-a-1#{i},c.{i}A>T,{float(i)}\n" for i in range(10)
)

#: Cross-dataset VRS lookup: one allele present in two score sets, as
#: GET /mapped-variants/vrs/{identifier} returns it (a bare list).
VRS_ID = "ga4gh:VA.KJ_post2"
VRS_ID_ENCODED = "ga4gh%3AVA.KJ_post2"
VRS_CROSS_DATASET_RAW: list[dict[str, Any]] = [
    {
        "variantUrn": VARIANT_URN,  # urn:mavedb:00000001-a-1#2
        "postMapped": {"id": VRS_ID},
        "clingenAlleleId": "CA000002",
        "current": True,
    },
    {
        "variantUrn": f"{SCORE_SET_URN_2}#5",
        "postMapped": {"id": VRS_ID},
        "clingenAlleleId": "CA000002",
        "current": True,
    },
]

#: A primary calibration record (urn + classification ids) as
#: GET /score-calibrations/score-set/{urn}/primary returns it.
CALIBRATION_URN = "urn:mavedb:calibration-test"
PRIMARY_CALIBRATION_RAW: dict[str, Any] = {
    **CALIBRATION_POS,
    "urn": CALIBRATION_URN,
    "scoreSetUrn": SCORE_SET_URN,
}

#: Variants grouped by functional-classification id, as
#: GET /score-calibrations/{urn}/variants returns them (249=abnormal, 250=normal).
CALIBRATION_VARIANTS_RAW: list[dict[str, Any]] = [
    {
        "functionalClassificationId": 249,
        "variants": [
            {
                "urn": f"{SCORE_SET_URN}#2",
                "hgvsNt": "c.2T>G",
                "hgvsPro": "p.Met1Arg",
                "data": {"score_data": {"score": 0.94}},
            }
        ],
    },
    {
        "functionalClassificationId": 250,
        "variants": [
            {
                "urn": f"{SCORE_SET_URN}#9",
                "hgvsNt": "c.9A>G",
                "hgvsPro": "p.Lys3Arg",
                "data": {"score_data": {"score": 3.5}},
            }
        ],
    },
]
