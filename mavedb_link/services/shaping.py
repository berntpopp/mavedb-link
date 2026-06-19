"""Response-mode projection for MaveDB entities (data-plane shaping).

The wrapper normalises MaveDB's verbose camelCase records into tidy snake_case
payloads, tiered by ``response_mode`` to control the per-call token cost:

- ``minimal``: identity anchors only.
- ``compact`` (default): the high-signal fields, with null/empty values dropped.
- ``standard`` / ``full``: the complete normalised record.

Shapers are pure functions over upstream dicts so they unit-test in isolation.
"""

from __future__ import annotations

from typing import Any

from mavedb_link.constants import MAVEDB_WEB_URL
from mavedb_link.identifiers import score_set_urn_of_variant

RESPONSE_MODES: tuple[str, ...] = ("minimal", "compact", "standard", "full")
DEFAULT_RESPONSE_MODE = "compact"


def _is_empty(value: Any) -> bool:
    """Whether a value should be dropped in compact mode."""
    return value is None or value == "" or value == [] or value == {}


def _drop_empty(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``payload`` with empty values removed (one level deep)."""
    return {k: v for k, v in payload.items() if not _is_empty(v)}


def _web_url(kind: str, urn: str | None) -> str | None:
    """Build a MaveDB web permalink for a record kind + URN."""
    return f"{MAVEDB_WEB_URL}/{kind}/{urn}" if urn else None


def _license_short(raw: dict[str, Any]) -> str | None:
    """Pull the per-record license short name (CC0 / CC BY 4.0 / ...)."""
    lic = raw.get("license") or {}
    return lic.get("shortName") if isinstance(lic, dict) else None


def _shape_publication(pub: dict[str, Any], *, full: bool) -> dict[str, Any]:
    """Normalise one publication identifier record."""
    base: dict[str, Any] = {
        "db_name": pub.get("dbName"),
        "identifier": pub.get("identifier"),
        "publication_year": pub.get("publicationYear"),
        "doi": pub.get("doi"),
    }
    if full:
        base.update(
            {
                "title": pub.get("title"),
                "journal": pub.get("publicationJournal"),
                "url": pub.get("url"),
                "authors": pub.get("authors"),
            }
        )
    return _drop_empty(base)


def _shape_publications(raw: dict[str, Any], *, full: bool) -> dict[str, Any]:
    """Group a record's primary + secondary publications."""
    primary = [
        _shape_publication(p, full=full) for p in raw.get("primaryPublicationIdentifiers") or []
    ]
    secondary = raw.get("secondaryPublicationIdentifiers") or []
    out: dict[str, Any] = {"primary": primary}
    if full:
        out["secondary"] = [_shape_publication(p, full=True) for p in secondary]
    else:
        out["secondary_count"] = len(secondary)
    return out


def _shape_target(target: dict[str, Any], *, full: bool) -> dict[str, Any]:
    """Normalise one target gene record."""
    seq = target.get("targetSequence") or {}
    taxonomy = seq.get("taxonomy") or {}
    accession = target.get("targetAccession") or {}
    base: dict[str, Any] = {
        "name": target.get("name"),
        "category": target.get("category"),
        "organism": taxonomy.get("organismName"),
    }
    if full:
        externals = []
        for ext in target.get("externalIdentifiers") or []:
            ident = ext.get("identifier") or {}
            externals.append(
                _drop_empty(
                    {
                        "db_name": ident.get("dbName"),
                        "identifier": ident.get("identifier"),
                        "offset": ext.get("offset"),
                    }
                )
            )
        base.update(
            {
                "sequence_type": seq.get("sequenceType"),
                "taxon_id": taxonomy.get("taxId") or taxonomy.get("code"),
                "accession": accession.get("accession"),
                "assembly": accession.get("assembly"),
                "external_identifiers": externals,
                "mapped_hgnc_name": target.get("mappedHgncName"),
                "uniprot_id": target.get("uniprotIdFromMappedMetadata"),
            }
        )
    return _drop_empty(base)


def shape_score_set(raw: dict[str, Any], response_mode: str) -> dict[str, Any]:
    """Project a score-set record to the requested verbosity."""
    if response_mode == "minimal":
        return _drop_empty({"urn": raw.get("urn"), "title": raw.get("title")})
    full = response_mode in ("standard", "full")
    payload: dict[str, Any] = {
        "urn": raw.get("urn"),
        "title": raw.get("title"),
        "short_description": raw.get("shortDescription"),
        "num_variants": raw.get("numVariants"),
        "license": _license_short(raw),
        "targets": [_shape_target(t, full=full) for t in raw.get("targetGenes") or []],
        "experiment_urn": (raw.get("experiment") or {}).get("urn") or raw.get("experimentUrn"),
        "publications": _shape_publications(raw, full=full),
        "processing_state": raw.get("processingState"),
        "published_date": raw.get("publishedDate"),
        "record_url": _web_url("score-sets", raw.get("urn")),
    }
    if full:
        payload.update(
            {
                "abstract_text": raw.get("abstractText"),
                "method_text": raw.get("methodText"),
                "dataset_columns": raw.get("datasetColumns"),
                "doi_identifiers": [d.get("identifier") for d in raw.get("doiIdentifiers") or []],
                "meta_analyzes_score_set_urns": raw.get("metaAnalyzesScoreSetUrns"),
                "meta_analyzed_by_score_set_urns": raw.get("metaAnalyzedByScoreSetUrns"),
                "superseded_score_set_urn": (raw.get("supersededScoreSet") or {}).get("urn"),
                "superseding_score_set_urn": (raw.get("supersedingScoreSet") or {}).get("urn"),
                "creation_date": raw.get("creationDate"),
                "modification_date": raw.get("modificationDate"),
                "mapping_state": raw.get("mappingState"),
                "private": raw.get("private"),
                "external_links": raw.get("externalLinks"),
                "official_collections": [
                    c.get("urn") for c in raw.get("officialCollections") or []
                ],
            }
        )
        return payload
    return _drop_empty(payload)


def shape_experiment(raw: dict[str, Any], response_mode: str) -> dict[str, Any]:
    """Project an experiment record to the requested verbosity."""
    if response_mode == "minimal":
        return _drop_empty({"urn": raw.get("urn"), "title": raw.get("title")})
    full = response_mode in ("standard", "full")
    payload: dict[str, Any] = {
        "urn": raw.get("urn"),
        "title": raw.get("title"),
        "short_description": raw.get("shortDescription"),
        "experiment_set_urn": raw.get("experimentSetUrn"),
        "score_set_urns": raw.get("scoreSetUrns"),
        "num_score_sets": raw.get("numScoreSets"),
        "keywords": _shape_keywords(raw.get("keywords") or []),
        "publications": _shape_publications(raw, full=full),
        "published_date": raw.get("publishedDate"),
        "record_url": _web_url("experiments", raw.get("urn")),
    }
    if full:
        payload.update(
            {
                "abstract_text": raw.get("abstractText"),
                "method_text": raw.get("methodText"),
                "doi_identifiers": [d.get("identifier") for d in raw.get("doiIdentifiers") or []],
                "creation_date": raw.get("creationDate"),
                "processing_state": raw.get("processingState"),
            }
        )
        return payload
    return _drop_empty(payload)


def _shape_keywords(keywords: list[Any]) -> list[Any]:
    """Flatten controlled-keyword records to ``label`` strings where possible."""
    out: list[Any] = []
    for kw in keywords:
        if isinstance(kw, dict):
            inner = kw.get("keyword")
            keyword = inner if isinstance(inner, dict) else kw
            label = keyword.get("label") or keyword.get("value") or keyword.get("key")
            if label:
                out.append(label)
        elif kw:
            out.append(kw)
    return out


def shape_gene(raw: dict[str, Any], response_mode: str) -> dict[str, Any]:
    """Project a /genes/{symbol} record (gene identity, score sets excluded)."""
    payload = {
        "symbol": raw.get("symbol"),
        "name": raw.get("name"),
        "hgnc_id": raw.get("hgncId"),
        "locus_group": raw.get("locusGroup"),
        "location": raw.get("location"),
        "omim_id": raw.get("omimId"),
        "ensembl_gene_id": raw.get("ensemblGeneId"),
    }
    return payload if response_mode in ("standard", "full") else _drop_empty(payload)


def shape_mapped_variant(raw: dict[str, Any], response_mode: str) -> dict[str, Any]:
    """Project a mapped-variant (VRS allele) record."""
    post = raw.get("postMapped") or {}
    payload: dict[str, Any] = {
        "variant_urn": raw.get("variantUrn") or (raw.get("variant") or {}).get("urn"),
        "vrs_id": post.get("id") if isinstance(post, dict) else None,
        "clingen_allele_id": raw.get("clingenAlleleId"),
        "current": raw.get("current"),
    }
    if response_mode in ("standard", "full"):
        payload.update(
            {
                "pre_mapped": raw.get("preMapped"),
                "post_mapped": post,
                "vrs_version": raw.get("vrsVersion"),
                "mapping_api_version": raw.get("mappingApiVersion"),
                "alignment_level": raw.get("alignmentLevel"),
            }
        )
        return payload
    return _drop_empty(payload)


def shape_single_variant(raw: dict[str, Any], response_mode: str) -> dict[str, Any]:
    """Project one variant record (``GET /variants/{urn}``) to its score + hgvs.

    The single-variant retrieval path (DEF-6): identity + the numeric ``score``
    in every mode; the full ``score_data``/``count_data`` and mapped alleles only
    in standard/full.
    """
    data = raw.get("data") or {}
    score_data = data.get("score_data") or {}
    variant_urn = raw.get("urn")
    score_set = raw.get("scoreSet") or {}
    score_set_urn = score_set.get("urn") if isinstance(score_set, dict) else None
    if not score_set_urn and isinstance(variant_urn, str):
        score_set_urn = score_set_urn_of_variant(variant_urn)
    payload: dict[str, Any] = {
        "variant_urn": variant_urn,
        "score_set_urn": score_set_urn,
        "hgvs_nt": raw.get("hgvsNt"),
        "hgvs_pro": raw.get("hgvsPro"),
        "score": score_data.get("score"),
    }
    if response_mode in ("standard", "full"):
        payload.update(
            {
                "score_data": score_data,
                "count_data": data.get("count_data"),
                "mapped_variants": [
                    shape_mapped_variant(m, response_mode) for m in raw.get("mappedVariants") or []
                ],
            }
        )
        return payload
    return _drop_empty(payload)


def shape_collection(raw: dict[str, Any], response_mode: str) -> dict[str, Any]:
    """Project a collection record."""
    if response_mode == "minimal":
        return _drop_empty({"urn": raw.get("urn"), "name": raw.get("name")})
    payload: dict[str, Any] = {
        "urn": raw.get("urn"),
        "name": raw.get("name"),
        "description": raw.get("description"),
        "badge_name": raw.get("badgeName"),
        "experiment_urns": raw.get("experimentUrns"),
        "score_set_urns": raw.get("scoreSetUrns"),
        "private": raw.get("private"),
    }
    return payload if response_mode in ("standard", "full") else _drop_empty(payload)
