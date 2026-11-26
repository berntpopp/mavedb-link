"""Builders for ``_meta.next_commands`` entries: ``{tool, arguments}`` steps.

The envelope-facing subset (``cmd``, ``default_error_next_commands``) is consumed
by the error boundary; the per-tool ``after_*`` chainers steer the success path
(find -> record -> scores -> genome mapping).
"""

from __future__ import annotations

import unicodedata
from typing import Any

from mavedb_link.constants import (
    MAX_CLASSIFIED_LIMIT,
    MAX_COLLECTION_LIMIT,
    MAX_FIND_LIMIT,
    MAX_GENE_LIMIT,
    MAX_MAPPED_LIMIT,
    MAX_SEARCH_LIMIT,
)
from mavedb_link.identifiers import looks_like_gene_symbol


def cmd(tool: str, **arguments: Any) -> dict[str, Any]:
    """One ready-to-call next step."""
    return {"tool": tool, "arguments": arguments}


def safe_echo(text: str | None) -> str | None:
    """The caller's free text if it may repeat in a page/widen step (GTS-CORE-TXT-04), else None.

    At most 200 characters and no control or format characters (Unicode Cc/Cf).
    """
    if not text or len(text) > 200:
        return None
    if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in text):
        return None
    return text


def _gene(value: Any) -> str | None:
    """``value`` when it passes the gene-symbol check, else None."""
    return value.strip() if isinstance(value, str) and looks_like_gene_symbol(value) else None


def widen_cmd(tool: str, base_args: dict[str, Any], total: int, ceiling: int) -> dict[str, Any]:
    """Re-run ``tool`` with ``limit`` raised to fit (capped at ``ceiling``)."""
    return cmd(tool, **{**base_args, "limit": min(total, ceiling)})


def page_offset_cmd(tool: str, base_args: dict[str, Any], next_offset: int) -> dict[str, Any]:
    """Fetch the next page by advancing ``offset`` (no rows re-sent)."""
    return cmd(tool, **{**base_args, "offset": next_offset})


def page_start_cmd(tool: str, base_args: dict[str, Any], next_start: int) -> dict[str, Any]:
    """Fetch the next page by advancing ``start`` (scores CSV paging)."""
    return cmd(tool, **{**base_args, "start": next_start})


def _more_offset(
    tool: str, base_args: dict[str, Any], payload: dict[str, Any], ceiling: int
) -> list[dict[str, Any]]:
    """Forward-page (offset) then widen step for a truncated list payload."""
    if not payload.get("truncated"):
        return []
    steps: list[dict[str, Any]] = []
    nxt = payload.get("next_offset")
    if nxt is not None:
        steps.append(page_offset_cmd(tool, base_args, int(nxt)))
    steps.append(widen_cmd(tool, base_args, int(payload.get("total") or ceiling), ceiling))
    return steps


def _page_search(tool: str, query: str | None, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Page/widen a text search; omitted when the query is not safe to repeat."""
    if not query:
        return _more_offset(tool, {}, payload, MAX_SEARCH_LIMIT)
    echo = safe_echo(query)
    return _more_offset(tool, {"text": echo}, payload, MAX_SEARCH_LIMIT) if echo else []


def default_error_next_commands(
    tool: str, error_code: str, arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    """A sensible recovery step for any error lacking an explicit fallback."""
    if error_code in ("upstream_unavailable", "rate_limited"):
        return [cmd("get_diagnostics")]
    # Only validated identifiers are forwarded (GTS-CORE-TXT-04): a failed URN or free
    # text never re-enters a step.
    if tool in ("get_score_set", "get_variant_scores", "get_mapped_variants", "get_experiment"):
        gene = _gene(arguments.get("urn"))  # a gene symbol passed as a URN
        first = cmd("search_score_sets", text=gene) if gene else cmd("search_score_sets")
        return [first, cmd("get_server_capabilities")]
    if tool == "find_variant" and (gene := _gene(arguments.get("gene_symbol"))):
        return [cmd("get_gene_score_sets", symbol=gene)]
    if tool == "get_gene_score_sets" and (gene := _gene(arguments.get("symbol"))):
        return [cmd("search_score_sets", text=gene)]
    return [cmd("get_server_capabilities")]


def after_capabilities() -> list[dict[str, Any]]:
    """After get_server_capabilities: start the canonical find->record workflow."""
    return [cmd("search_score_sets", text="BRCA1"), cmd("get_diagnostics")]


def after_diagnostics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """After get_diagnostics: begin discovery if the API is reachable."""
    if payload.get("api_reachable"):
        return [cmd("search_score_sets", text="BRCA1")]
    return [cmd("get_server_capabilities")]


def _top_urn(payload: dict[str, Any], key: str) -> str | None:
    """URN of the first result in a list payload (or None)."""
    results = payload.get(key) or []
    if results and isinstance(results[0], dict):
        return results[0].get("urn")
    return None


def after_search_score_sets(query: str | None, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """After search_score_sets: open the top hit; page/widen if truncated."""
    top = _top_urn(payload, "results")
    if not top:
        gene = _gene(query) or "BRCA1"
        return [cmd("get_gene_score_sets", symbol=gene), cmd("get_server_capabilities")]
    steps = [cmd("get_score_set", urn=top)]
    steps += _page_search("search_score_sets", query, payload)
    return steps


def after_search_experiments(query: str | None, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """After search_experiments: open the top hit; page/widen if truncated."""
    top = _top_urn(payload, "results")
    if not top:
        gene = _gene(query)
        first = cmd("search_score_sets", text=gene) if gene else cmd("search_score_sets")
        return [first, cmd("get_server_capabilities")]
    steps = [cmd("get_experiment", urn=top)]
    steps += _page_search("search_experiments", query, payload)
    return steps


def after_get_score_set(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """After get_score_set: pull the scores, then the genome-mapped alleles."""
    urn = payload.get("urn")
    if not urn:
        return [cmd("get_server_capabilities")]
    steps = [cmd("get_variant_scores", urn=urn), cmd("get_mapped_variants", urn=urn)]
    exp = payload.get("experiment_urn")
    if exp:
        steps.append(cmd("get_experiment", urn=exp))
    # GAP-E: a member set's official_collections is the in-surface discovery route to
    # get_collection (MaveDB exposes no collection search/list endpoint), so a valid
    # collection URN reaches the agent here rather than being unreachable.
    collections = payload.get("official_collections") or []
    if collections:
        steps.append(cmd("get_collection", urn=collections[0]))
    return steps


def after_get_variant_scores(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """After get_variant_scores: page forward if truncated, then offer mapping."""
    urn = payload.get("urn")
    steps: list[dict[str, Any]] = []
    if payload.get("truncated") and payload.get("next_start") is not None and urn:
        steps.append(page_start_cmd("get_variant_scores", {"urn": urn}, int(payload["next_start"])))
    if urn:
        steps.append(cmd("get_mapped_variants", urn=urn))
    return steps or [cmd("get_server_capabilities")]


def after_get_variant_score(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """After get_variant_score: roll the variant up across score sets, then context.

    The cross-dataset rollup (find_variant by variant_urn) is offered first: it
    answers "every assay that measured this variant" in one hop, with no map-first
    step (the consolidation win, 2.2).
    """
    steps: list[dict[str, Any]] = []
    variants = payload.get("variants") or []
    if variants and isinstance(variants[0], dict) and variants[0].get("variant_urn"):
        steps.append(cmd("find_variant", variant=variants[0]["variant_urn"]))
    score_set = payload.get("score_set_urn") or payload.get("urn")
    if score_set:
        steps.append(cmd("get_score_set", urn=score_set))
        steps.append(cmd("get_mapped_variants", urn=score_set))
    return steps or [cmd("get_server_capabilities")]


def after_get_mapped_variants(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """After get_mapped_variants: page forward if truncated, then open the record."""
    urn = payload.get("urn")
    steps = _more_offset(
        "get_mapped_variants", {"urn": urn} if urn else {}, payload, MAX_MAPPED_LIMIT
    )
    if urn:
        steps.append(cmd("get_score_set", urn=urn))
    return steps or [cmd("get_server_capabilities")]


def after_get_experiment(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """After get_experiment: open the first child score set."""
    score_sets = payload.get("score_set_urns") or []
    if score_sets:
        return [cmd("get_score_set", urn=score_sets[0])]
    return [cmd("search_score_sets"), cmd("get_server_capabilities")]


def after_get_gene_score_sets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """After get_gene_score_sets: open the first dataset; page if truncated."""
    top = _top_urn(payload, "score_sets")
    symbol = (payload.get("gene") or {}).get("symbol")
    if not top:
        return [cmd("search_score_sets", text=symbol or ""), cmd("get_server_capabilities")]
    steps = [cmd("get_score_set", urn=top)]
    steps += _more_offset(
        "get_gene_score_sets", {"symbol": symbol} if symbol else {}, payload, MAX_GENE_LIMIT
    )
    return steps


def after_get_collection(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """After get_collection: open the first member score set; page on if truncated."""
    score_sets = payload.get("score_set_urns") or []
    steps: list[dict[str, Any]] = []
    if score_sets:
        steps.append(cmd("get_score_set", urn=score_sets[0]))
    urn = payload.get("urn")
    steps += _more_offset(
        "get_collection", {"urn": urn} if urn else {}, payload, MAX_COLLECTION_LIMIT
    )
    return steps or [cmd("search_score_sets"), cmd("get_server_capabilities")]


def after_find_variant(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """After find_variant: open the first hit's score set + score; page if truncated."""
    hits = payload.get("hits") or []
    steps: list[dict[str, Any]] = []
    if hits and isinstance(hits[0], dict):
        first = hits[0]
        if first.get("score_set_urn"):
            steps.append(cmd("get_score_set", urn=first["score_set_urn"]))
        if first.get("variant_urn"):
            steps.append(cmd("get_variant_score", urn=first["variant_urn"]))
    vrs = payload.get("vrs_id")
    steps += _more_offset("find_variant", {"variant": vrs} if vrs else {}, payload, MAX_FIND_LIMIT)
    return steps or [cmd("get_server_capabilities")]


def after_get_score_distribution(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """After get_score_distribution: open the record + the underlying score table."""
    urn = payload.get("urn")
    if not urn:
        return [cmd("get_server_capabilities")]
    return [cmd("get_score_set", urn=urn), cmd("get_variant_scores", urn=urn)]


def after_get_hgvs_validation(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """After get_hgvs_validation: search for a relevant score set when valid."""
    if payload.get("valid"):
        return [cmd("search_score_sets"), cmd("get_server_capabilities")]
    return [cmd("get_server_capabilities")]


def after_get_classified_variants(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """After get_classified_variants: open the first variant + the score set; page on."""
    urn = payload.get("urn")
    steps: list[dict[str, Any]] = []
    variants = payload.get("variants") or []
    if variants and isinstance(variants[0], dict) and variants[0].get("variant_urn"):
        steps.append(cmd("get_variant_score", urn=variants[0]["variant_urn"]))
    if urn:
        steps.append(cmd("get_score_set", urn=urn))
        base = {"urn": urn}
        if payload.get("classification"):
            base["classification"] = payload["classification"]
        steps += _more_offset("get_classified_variants", base, payload, MAX_CLASSIFIED_LIMIT)
    return steps or [cmd("get_server_capabilities")]
