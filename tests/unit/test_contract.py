"""Contract suite: freezes the assessment §1 wins so they cannot silently regress.

Each test guards one cross-cutting invariant the remediation established
(gene-completeness, single-variant path, facet honesty, real totals, provenance,
null-trimming, and the error-envelope shape). One named test per guarantee.
"""

from __future__ import annotations

from typing import Any

import httpx
import respx

from tests import fixtures

BASE = fixtures.BASE_URL


def _mock_core(router: respx.Router) -> None:
    """Happy-path routes, with the target search returning a set the gene endpoint lacks."""
    router.get("/genes/UBE2I").mock(return_value=httpx.Response(200, json=fixtures.GENE_RESPONSE))
    router.post("/score-sets/search").mock(
        return_value=httpx.Response(200, json=fixtures.GENE_TARGET_SEARCH_RESPONSE)
    )
    router.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_RAW)
    )
    router.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.SCORES_CSV)
    )
    router.get(f"/variants/{fixtures.VARIANT_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.VARIANT_RAW)
    )
    router.get("/api/version").mock(
        return_value=httpx.Response(200, json=fixtures.API_VERSION_RESPONSE)
    )


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_gene_completeness_is_a_superset_of_target_search(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    # DEF-1 invariant: get_gene_score_sets ⊇ search(targets=[gene]).
    _mock_core(respx_mock)
    gene = structured(await facade.call_tool("get_gene_score_sets", {"symbol": "UBE2I"}))
    gene_urns = {s["urn"] for s in gene["score_sets"]}
    target = structured(await facade.call_tool("search_score_sets", {"targets": ["UBE2I"]}))
    target_urns = {s["urn"] for s in target["results"]}
    assert target_urns <= gene_urns
    assert gene["coverage"]["union"] == len(gene_urns)


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_single_variant_path_returns_score_in_one_call(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    # DEF-6: a variant's score is retrievable without paging — by URN and by hgvs.
    _mock_core(respx_mock)
    by_urn = structured(await facade.call_tool("get_variant_score", {"urn": fixtures.VARIANT_URN}))
    assert by_urn["score"] == -1.2
    by_hgvs = structured(
        await facade.call_tool(
            "get_variant_score", {"urn": fixtures.SCORE_SET_URN, "hgvs": "c.2T>G"}
        )
    )
    assert by_hgvs["matches"][0]["score"] == -1.2


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_variant_scores_total_is_a_real_integer(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    # DEF-7: total == num_variants (never null when the record is reachable).
    _mock_core(respx_mock)
    payload = structured(
        await facade.call_tool("get_variant_scores", {"urn": fixtures.SCORE_SET_URN})
    )
    assert payload["total"] == fixtures.SCORE_SET_RAW["numVariants"]
    assert payload["offset"] == payload["start"]


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_facet_honesty_note_is_emitted(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    # DEF-3: an organism facet that drops a known-mismatch record reports it.
    def _ss(urn: str, organism: str | None) -> dict[str, Any]:
        taxonomy = {"organismName": organism} if organism is not None else {}
        return {
            "urn": urn,
            "title": "x",
            "targetGenes": [{"name": "BRCA2", "targetSequence": {"taxonomy": taxonomy}}],
        }

    respx_mock.post("/score-sets/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "scoreSets": [
                    _ss("urn:mavedb:1-a-1", None),
                    _ss("urn:mavedb:2-a-1", "Mus musculus"),
                ],
                "numScoreSets": 2,
            },
        )
    )
    payload = structured(
        await facade.call_tool(
            "search_score_sets", {"text": "BRCA2", "target_organism_names": ["Homo sapiens"]}
        )
    )
    assert {r["urn"] for r in payload["results"]} == {
        "urn:mavedb:1-a-1"
    }  # null kept, mouse dropped
    assert payload["_meta"]["facet_excluded"]["target_organism_names"] == 1


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_provenance_git_sha_is_real_in_discovery(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    # DEF-9: both discovery tools expose a real, labeled git_sha.
    _mock_core(respx_mock)
    diag = structured(await facade.call_tool("get_diagnostics", {}))
    assert diag["build"]["git_sha"] not in (None, "", "unknown")
    assert diag["build"]["git_sha_source"] in ("env", "git", "source_tree")


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_compact_payloads_carry_no_nulls(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    # DEF-10: compact mode trims null fields (outside _meta).
    _mock_core(respx_mock)
    payload = structured(
        await facade.call_tool(
            "get_score_set", {"urn": fixtures.SCORE_SET_URN, "response_mode": "compact"}
        )
    )
    nulls = [k for k, v in payload.items() if v is None and k != "_meta"]
    assert nulls == [], f"compact payload leaked null fields: {nulls}"


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_error_envelope_contract_shape(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    # The error plane (10/10) must stay uniform.
    respx_mock.get("/score-sets/urn:mavedb:09999999-a-1").mock(
        return_value=httpx.Response(404, json={"detail": "missing"})
    )
    payload = structured(
        await facade.call_tool("get_score_set", {"urn": "urn:mavedb:09999999-a-1"})
    )
    assert payload["success"] is False
    for key in ("error_code", "message", "retryable", "recovery_action"):
        assert key in payload
    assert payload["_meta"]["request_id"]
    assert payload["_meta"]["next_commands"]
