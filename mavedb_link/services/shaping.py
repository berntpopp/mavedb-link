"""Response-mode projection for MaveDB entities (data-plane shaping).

The wrapper normalises MaveDB's verbose camelCase records into tidy snake_case
payloads, tiered by ``response_mode`` to control the per-call token cost:

- ``minimal``: identity anchors only.
- ``compact`` (default): the high-signal fields, with null/empty values dropped.
- ``standard``: the structured record -- identifiers, lineage, dates, capped
  author lists (first author + count) -- but NOT the heavy free-text blobs.
- ``full``: the complete record incl. abstract/method text, dataset columns,
  score ranges, and the full author lists.

Shapers are pure functions over upstream dicts so they unit-test in isolation.
"""

from __future__ import annotations

from typing import Any

from mavedb_link.constants import MAVEDB_WEB_URL
from mavedb_link.identifiers import score_set_urn_of_variant, variant_index_of
from mavedb_link.services.calibration import coerce_score, shape_calibrations

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


def _first_author(authors: Any) -> str | None:
    """The first author's name from a publication's author list (or ``None``)."""
    if isinstance(authors, list) and authors:
        first = authors[0]
        if isinstance(first, dict):
            return first.get("name")
        if isinstance(first, str):
            return first
    return None


def _shape_publication(pub: dict[str, Any], *, detail: str) -> dict[str, Any]:
    """Normalise one publication identifier record, tiered by ``detail``.

    ``compact`` keeps the citation anchors; ``standard`` adds title/journal/url and
    caps the author list to ``first_author`` + ``author_count`` (the full list is
    ~2.7 KB/record in search results); ``full`` adds the complete ``authors`` list.
    """
    base: dict[str, Any] = {
        "db_name": pub.get("dbName"),
        "identifier": pub.get("identifier"),
        "publication_year": pub.get("publicationYear"),
        "doi": pub.get("doi"),
    }
    if detail in ("standard", "full"):
        authors = pub.get("authors") or []
        base.update(
            {
                "title": pub.get("title"),
                "journal": pub.get("publicationJournal"),
                "url": pub.get("url"),
                "first_author": _first_author(authors),
                "author_count": len(authors) if isinstance(authors, list) else None,
            }
        )
    if detail == "full":
        base["authors"] = pub.get("authors")
    return _drop_empty(base)


def _shape_publications(raw: dict[str, Any], *, detail: str) -> dict[str, Any]:
    """Group a record's primary + secondary publications (tiered by ``detail``)."""
    primary = [
        _shape_publication(p, detail=detail) for p in raw.get("primaryPublicationIdentifiers") or []
    ]
    secondary = raw.get("secondaryPublicationIdentifiers") or []
    out: dict[str, Any] = {"primary": primary}
    if detail == "full":
        out["secondary"] = [_shape_publication(p, detail="full") for p in secondary]
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


def _listing_target_names(targets: list[Any]) -> list[str]:
    """Target gene NAMES only (the discovery-listing projection).

    A listing row needs target identity, not the full per-target block — organism,
    accession, external ids, uniprot live at the record via get_score_set. For a
    multi-target assay (e.g. VarChAMP, 28 targets) this is the difference between 28
    short strings and 28 nested objects per row, repeated across every dataset.
    """
    names: list[str] = []
    for target in targets:
        if isinstance(target, dict) and target.get("name"):
            names.append(target["name"])
    return names


def shape_score_set(
    raw: dict[str, Any], response_mode: str, *, listing: bool = False
) -> dict[str, Any]:
    """Project a score-set record to the requested verbosity.

    ``listing=True`` is the discovery projection (``search_score_sets`` /
    ``get_gene_score_sets``): the heavy curated ``score_calibrations`` ladder is
    replaced by a lightweight ``has_calibrations`` presence flag (the full ladder is
    record-level data, fetched via ``get_score_set``), and ``targets`` collapses to
    gene-name strings. ``listing=False`` (the record call) keeps both in full.
    """
    if response_mode == "minimal":
        return _drop_empty({"urn": raw.get("urn"), "title": raw.get("title")})
    full = response_mode == "full"
    rich = response_mode in ("standard", "full")
    targets = raw.get("targetGenes") or []
    payload: dict[str, Any] = {
        "urn": raw.get("urn"),
        "title": raw.get("title"),
        "short_description": raw.get("shortDescription"),
        "num_variants": raw.get("numVariants"),
        "license": _license_short(raw),
        "targets": (
            _listing_target_names(targets)
            if listing
            else [_shape_target(t, full=rich) for t in targets]
        ),
        "experiment_urn": (raw.get("experiment") or {}).get("urn") or raw.get("experimentUrn"),
        "publications": _shape_publications(raw, detail=response_mode),
        "processing_state": raw.get("processingState"),
        "published_date": raw.get("publishedDate"),
        "record_url": _web_url("score-sets", raw.get("urn")),
    }
    # MaveDB's curated interpretation layer (ACMG/OddsPath/thresholds): inline the
    # full ladder on the RECORD call; on a discovery LISTING ship only a presence
    # flag (token discipline — the ladder is get_score_set territory). Absent for
    # the MINORITY of sets that carry no calibrations.
    if listing:
        if raw.get("scoreCalibrations"):
            payload["has_calibrations"] = True
    else:
        payload["score_calibrations"] = shape_calibrations(raw.get("scoreCalibrations"), full=full)
    if rich:
        # The structured record (standard+full): identifiers, lineage, dates -- not
        # the heavy free-text blobs.
        payload.update(
            {
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
    if full:
        # The heavy free text + score ranges live at full only (F8).
        payload.update(
            {
                "score_ranges": raw.get("scoreRanges"),
                "abstract_text": raw.get("abstractText"),
                "method_text": raw.get("methodText"),
                "dataset_columns": raw.get("datasetColumns"),
            }
        )
    if rich:
        return payload
    return _drop_empty(payload)


def shape_experiment(raw: dict[str, Any], response_mode: str) -> dict[str, Any]:
    """Project an experiment record to the requested verbosity."""
    if response_mode == "minimal":
        return _drop_empty({"urn": raw.get("urn"), "title": raw.get("title")})
    full = response_mode == "full"
    rich = response_mode in ("standard", "full")
    payload: dict[str, Any] = {
        "urn": raw.get("urn"),
        "title": raw.get("title"),
        "short_description": raw.get("shortDescription"),
        "experiment_set_urn": raw.get("experimentSetUrn"),
        "score_set_urns": raw.get("scoreSetUrns"),
        "num_score_sets": raw.get("numScoreSets"),
        "keywords": _shape_keywords(raw.get("keywords") or []),
        "publications": _shape_publications(raw, detail=response_mode),
        "published_date": raw.get("publishedDate"),
        "record_url": _web_url("experiments", raw.get("urn")),
    }
    if rich:
        payload.update(
            {
                "doi_identifiers": [d.get("identifier") for d in raw.get("doiIdentifiers") or []],
                "creation_date": raw.get("creationDate"),
                "processing_state": raw.get("processingState"),
            }
        )
    if full:
        payload.update(
            {
                "abstract_text": raw.get("abstractText"),
                "method_text": raw.get("methodText"),
            }
        )
    if rich:
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
    if response_mode == "minimal":
        return _drop_empty({"symbol": raw.get("symbol"), "hgnc_id": raw.get("hgncId")})
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


def _summarize_vrs(post: Any) -> dict[str, Any]:
    """Flatten a post-mapped VRS allele to its genomic coordinates (defensive).

    Tolerates VRS 1.x/2.x shape differences and returns only the keys it can parse
    (never raises). Keys: assembly, sequence_id, start, end, ref, alt.
    """
    if not isinstance(post, dict):
        return {}
    loc = post.get("location") or {}
    ref_seq = loc.get("sequenceReference") or {}
    interval = loc.get("interval") or {}
    state = post.get("state") or {}
    summary: dict[str, Any] = {
        "assembly": ref_seq.get("assembly"),
        "sequence_id": ref_seq.get("refgetAccession") or loc.get("sequence_id"),
        "start": loc.get("start") if "start" in loc else interval.get("start"),
        "end": loc.get("end") if "end" in loc else interval.get("end"),
        "ref": state.get("referenceSequence"),
        "alt": state.get("sequence"),
    }
    return {k: v for k, v in summary.items() if v is not None}


def shape_mapped_variant(raw: dict[str, Any], response_mode: str) -> dict[str, Any]:
    """Project a mapped-variant (VRS allele) record.

    standard returns a FLAT post_mapped genomic summary (dropping the verbose
    pre_mapped/post_mapped VRS objects); full keeps the complete objects.
    """
    post = raw.get("postMapped") or {}
    variant_urn = raw.get("variantUrn") or (raw.get("variant") or {}).get("urn")
    payload: dict[str, Any] = {
        "variant_urn": variant_urn,
        # The numeric join key: align with get_variant_scores rows by this value,
        # NOT by row position (some variants are unmapped, so the lists differ).
        "variant_index": variant_index_of(variant_urn) if variant_urn else None,
        "vrs_id": post.get("id") if isinstance(post, dict) else None,
        "clingen_allele_id": raw.get("clingenAlleleId"),
        "current": raw.get("current"),
    }
    if response_mode == "full":
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
    if response_mode == "standard":
        summary = _summarize_vrs(post)
        if summary:
            payload["post_mapped"] = summary
        post_hgvs = raw.get("postMappedHgvs") or raw.get("post_mapped_hgvs")
        if post_hgvs:
            payload["post_mapped_hgvs"] = post_hgvs
        for key, value in (
            ("vrs_version", raw.get("vrsVersion")),
            ("alignment_level", raw.get("alignmentLevel")),
        ):
            if value is not None:
                payload[key] = value
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
        "variant_index": variant_index_of(variant_urn) if isinstance(variant_urn, str) else None,
        "score_set_urn": score_set_urn,
        "hgvs_nt": raw.get("hgvsNt"),
        "hgvs_pro": raw.get("hgvsPro"),
        # Coerce to float: the variant record can serialise score as a string for
        # some sets, which would otherwise crash the classifier (GAP-2).
        "score": coerce_score(score_data.get("score")),
    }
    if response_mode in ("standard", "full"):
        # Embedded mappings are current-only except at full -- the by-URN path used
        # to leak superseded current:false rows (F2).
        mapped = raw.get("mappedVariants") or []
        if response_mode != "full":
            mapped = [m for m in mapped if isinstance(m, dict) and m.get("current")]
        payload.update(
            {
                "score_data": score_data,
                "count_data": data.get("count_data"),
                "mapped_variants": [shape_mapped_variant(m, response_mode) for m in mapped],
            }
        )
        return payload
    return _drop_empty(payload)


def shape_collection(
    raw: dict[str, Any], response_mode: str, *, limit: int, offset: int
) -> dict[str, Any]:
    """Project a collection record, paging its member lists (F12).

    The member ``score_set_urns`` (and ``experiment_urns``) are windowed by
    ``offset``/``limit`` so a large collection is not dumped inline; the pagination
    block describes the primary ``score_set_urns`` list. ``num_score_sets`` /
    ``num_experiments`` carry the true totals.
    """
    if response_mode == "minimal":
        return _drop_empty({"urn": raw.get("urn"), "name": raw.get("name")})
    score_set_urns = raw.get("scoreSetUrns") or []
    experiment_urns = raw.get("experimentUrns") or []
    total = len(score_set_urns)
    page = score_set_urns[offset : offset + limit]
    truncated = offset + len(page) < total
    payload: dict[str, Any] = {
        "urn": raw.get("urn"),
        "name": raw.get("name"),
        "description": raw.get("description"),
        "badge_name": raw.get("badgeName"),
        "num_experiments": len(experiment_urns),
        "num_score_sets": total,
        "experiment_urns": experiment_urns[offset : offset + limit],
        "score_set_urns": page,
        "private": raw.get("private"),
        "total": total,
        "returned": len(page),
        "limit": limit,
        "offset": offset,
        "truncated": truncated,
        "next_offset": offset + len(page) if truncated else None,
    }
    return payload if response_mode in ("standard", "full") else _drop_empty(payload)
