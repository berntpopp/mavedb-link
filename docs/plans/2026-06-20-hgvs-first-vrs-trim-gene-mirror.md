# HGVS-first / VRS-trim / mirrored-gene-hop Implementation Plan

> Historical record — this document records the implementation plan as of its date. Current
> behavior is defined by implemented code, standards, release evidence, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `find_variant` resolve a bare HGVS string (+ optional gene) to its VRS internally, trim redundant VRS objects at `standard`, and remove the live `/genes` latency floor from `get_gene_score_sets`.

**Architecture:** Mirror-first, live-backup. A new mirror `hgvs_index` (schema v2) resolves HGVS→variant→VRS offline; a capped live probe is the fallback. `shape_mapped_variant` re-tiers `standard` to a flat genomic summary. The gene listing is served from the mirror while gene identity is process-cached + time-boxed. Two-plane boundary preserved: data plane returns dicts / raises typed exceptions; `run_mcp_tool` owns the envelope.

**Tech Stack:** Python 3.12+, FastMCP 3.x, SQLite (stdlib `sqlite3`), `uv`, `pytest`, `ruff`, `mypy --strict`.

## Global Constraints

- Python 3.12+; modern typing (`X | None`, builtin generics). Run via `uv run`.
- 600-LOC per module (`make lint-loc`). `mypy --strict` + `ruff` must pass.
- TDD: failing test → see it fail → minimal impl → see it pass → commit.
- Two-plane boundary: `services/`, `data/`, `ingest/` raise typed exceptions from `mavedb_link.exceptions`; never build envelopes.
- Mirror invariant: mirror-served and live-served payloads are interchangeable in shape; the mirror only changes latency/provenance.
- `capabilities.TOOLS` must equal the registered tool set (no new tool here) — `tests/unit/test_tool_names.py` stays green.
- Bump `MIRROR_SCHEMA_VERSION` on any mirror shape change; old mirrors auto-reject and degrade to live.
- Final gate: `make ci-local` then `make eval`.

---

### Task 1: Mirror schema v2 — `hgvs_index` + version bump

**Files:**
- Modify: `mavedb_link/constants.py:24` (`MIRROR_SCHEMA_VERSION = 1` → `2`)
- Modify: `mavedb_link/data/schema.sql` (add `hgvs_index` + HGVS indexes)
- Modify: `mavedb_link/services/scores.py` (promote `hgvs_core` public helper)
- Modify: `mavedb_link/ingest/parsing.py` (add `extract_hgvs_rows`)
- Modify: `mavedb_link/ingest/builder.py:147-159` (insert `hgvs_index` rows)
- Test: `tests/unit/test_ingest_hgvs_index.py` (new)

**Interfaces:**
- Produces: `scores.hgvs_core(value: str) -> str` (lowercased, accession-prefix-stripped body); `parsing.extract_hgvs_rows(scores_csv: str, score_set_urn: str) -> list[dict[str, Any]]` with keys `score_set_urn, variant_urn, hgvs_nt, hgvs_pro, hgvs_splice` (all normalized via `hgvs_core`, `None`-dropped).
- Consumes (later tasks): the `hgvs_index` table and `MIRROR_SCHEMA_VERSION == 2`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_ingest_hgvs_index.py
"""hgvs_index is populated from the scores CSV during the mirror build (schema v2)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from mavedb_link.constants import MIRROR_SCHEMA_VERSION
from mavedb_link.ingest.parsing import extract_hgvs_rows


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
            "hgvs_nt": "c.8168a>g",          # prefix-stripped + lowercased
            "hgvs_pro": "p.asp2723his",
            "hgvs_splice": None,
        }
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_ingest_hgvs_index.py -v`
Expected: FAIL — `MIRROR_SCHEMA_VERSION == 2` assertion fails and `extract_hgvs_rows` does not exist (ImportError).

- [ ] **Step 3a: Bump the schema version**

In `mavedb_link/constants.py` change line 24:

```python
MIRROR_SCHEMA_VERSION = 2
```

- [ ] **Step 3b: Add the table + indexes to `schema.sql`**

Append to `mavedb_link/data/schema.sql` (after the `mapped_variant` block, before `score_distribution`):

```sql
-- Target-relative HGVS -> variant identity (from the scores CSV), for HGVS-first
-- cross-dataset entry. Values are normalised for lookup: accession-prefix stripped
-- and lowercased (see scores.hgvs_core), so the resolver matches by equality.
CREATE TABLE hgvs_index (
    score_set_urn  TEXT,
    variant_urn    TEXT,
    hgvs_nt        TEXT,
    hgvs_pro       TEXT,
    hgvs_splice    TEXT
);
CREATE INDEX idx_hgvs_nt ON hgvs_index (hgvs_nt);
CREATE INDEX idx_hgvs_pro ON hgvs_index (hgvs_pro);
CREATE INDEX idx_hgvs_splice ON hgvs_index (hgvs_splice);

-- Genomic post-mapped HGVS lookups for the accessioned-HGVS resolution path
-- (case-insensitive: genomic accessions vary in case across sources).
CREATE INDEX idx_mapped_hgvs_g ON mapped_variant (post_mapped_hgvs_g COLLATE NOCASE);
CREATE INDEX idx_mapped_hgvs_c ON mapped_variant (post_mapped_hgvs_c COLLATE NOCASE);
CREATE INDEX idx_mapped_hgvs_p ON mapped_variant (post_mapped_hgvs_p COLLATE NOCASE);
```

- [ ] **Step 3c: Promote `hgvs_core` to a public helper**

In `mavedb_link/services/scores.py` rename `_hgvs_core` to a public `hgvs_core` that also lowercases, and update its one caller (`hgvs_matches`). Replace lines 61-68 and the use in 84/90:

```python
def hgvs_core(value: str) -> str:
    """The lowercased HGVS body without an accession prefix (part after the last ``:``).

    MaveDB stores hgvs_nt accession-prefixed in many sets
    (``ENST00000380152.8:c.8168A>G``), so comparing the prefix-stripped, lowercased
    body lets a bare ``c.8168A>G`` resolve the prefixed stored value and vice-versa
    (F5). Used by both the live by-hgvs scan and the mirror hgvs_index build/lookup.
    """
    return value.rsplit(":", 1)[-1].strip().lower()
```

Then in `hgvs_matches` (lines 84-90) use `hgvs_core`:

```python
    query_core = hgvs_core(query)
    for column in ("hgvs_nt", "hgvs_pro"):
        value = row.get(column)
        if not isinstance(value, str):
            continue
        if hgvs_core(value) == query_core or value.strip().lower() == query:
            return True
    return False
```

(`query` is already lowercased by the caller; `hgvs_core` lowercases too, so this is idempotent.)

- [ ] **Step 3d: Add `extract_hgvs_rows` to `parsing.py`**

In `mavedb_link/ingest/parsing.py`, import the helper and add the extractor. Update the import on line 18:

```python
from mavedb_link.services.scores import hgvs_core, parse_scores_csv
```

Add after `extract_scores` (line 67):

```python
def extract_hgvs_rows(scores_csv: str, score_set_urn: str) -> list[dict[str, Any]]:
    """Normalised (variant_urn, hgvs_*) rows for the mirror hgvs_index.

    Keeps only rows naming a variant (``accession``) AND carrying at least one HGVS
    field; each HGVS is stored as its :func:`hgvs_core` (prefix-stripped, lowercased)
    so the resolver matches by equality on an indexed column.
    """
    _, rows = parse_scores_csv(scores_csv)
    out: list[dict[str, Any]] = []
    for row in rows:
        accession = row.get("accession")
        if not isinstance(accession, str):
            continue
        nt = row.get("hgvs_nt")
        pro = row.get("hgvs_pro")
        splice = row.get("hgvs_splice")
        if not any(isinstance(v, str) for v in (nt, pro, splice)):
            continue
        out.append(
            {
                "score_set_urn": score_set_urn,
                "variant_urn": accession,
                "hgvs_nt": hgvs_core(nt) if isinstance(nt, str) else None,
                "hgvs_pro": hgvs_core(pro) if isinstance(pro, str) else None,
                "hgvs_splice": hgvs_core(splice) if isinstance(splice, str) else None,
            }
        )
    return out
```

- [ ] **Step 3e: Insert `hgvs_index` rows in the builder**

In `mavedb_link/ingest/builder.py`, import `extract_hgvs_rows` (line 25-30 import block):

```python
from mavedb_link.ingest.parsing import (
    compute_distribution,
    denamespace_csv,
    extract_hgvs_rows,
    extract_scores,
    parse_annotations,
)
```

Then in `_insert_score_set`, inside the `if scores_csv is not None:` block (after the distribution insert, around line 146), add:

```python
        hgvs_rows = extract_hgvs_rows(scores_csv, urn)
        if hgvs_rows:
            con.executemany(
                "INSERT INTO hgvs_index (score_set_urn, variant_urn, hgvs_nt, hgvs_pro, "
                "hgvs_splice) VALUES (:score_set_urn, :variant_urn, :hgvs_nt, :hgvs_pro, "
                ":hgvs_splice)",
                hgvs_rows,
            )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_ingest_hgvs_index.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Verify the full build still works against the existing fixture**

Run: `uv run pytest tests/unit -k "builder or ingest" -v`
Expected: PASS — confirm no existing build test regressed (the fixture dump now also yields `hgvs_index` rows).

- [ ] **Step 6: Commit**

```bash
git add mavedb_link/constants.py mavedb_link/data/schema.sql mavedb_link/services/scores.py mavedb_link/ingest/parsing.py mavedb_link/ingest/builder.py tests/unit/test_ingest_hgvs_index.py
git commit -m "feat(mirror): hgvs_index + schema v2 for HGVS-first resolution"
```

---

### Task 2: Repository — `resolve_hgvs` + `gene_identity`

**Files:**
- Modify: `mavedb_link/data/repository.py` (add two methods)
- Test: `tests/unit/test_repository_hgvs.py` (new)

**Interfaces:**
- Consumes: `hgvs_index`, `gene_index`, `mapped_variant` tables (Task 1).
- Produces:
  - `MirrorRepository.resolve_hgvs(hgvs_core_value: str, *, gene: str | None = None) -> list[dict[str, Any]]` → rows `{variant_urn, score_set_urn, vrs_id}` (vrs_id may be `None` when a variant is unmapped). `hgvs_core_value` is the caller-normalised core; `gene` scopes via `gene_index`.
  - `MirrorRepository.gene_identity(symbol: str) -> dict[str, Any] | None` → `{symbol, organism}` from `gene_index`, or `None` when the symbol is absent.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_repository_hgvs.py
"""resolve_hgvs and gene_identity over a hand-built v2 mirror."""
from __future__ import annotations

import sqlite3

import pytest

from mavedb_link.data.repository import MirrorRepository


@pytest.fixture
def repo() -> MirrorRepository:
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE gene_index (gene_symbol_upper TEXT, gene_symbol TEXT,
            score_set_urn TEXT, organism TEXT, category TEXT);
        CREATE TABLE hgvs_index (score_set_urn TEXT, variant_urn TEXT,
            hgvs_nt TEXT, hgvs_pro TEXT, hgvs_splice TEXT);
        CREATE TABLE mapped_variant (variant_urn TEXT, score_set_urn TEXT, vrs_id TEXT,
            clingen_allele_id TEXT, post_mapped_hgvs_g TEXT, post_mapped_hgvs_p TEXT,
            post_mapped_hgvs_c TEXT);
        INSERT INTO gene_index VALUES ('BRCA1','BRCA1','urn:mavedb:1-a-1','Homo sapiens','protein_coding');
        INSERT INTO gene_index VALUES ('TP53','TP53','urn:mavedb:2-a-1','Homo sapiens','protein_coding');
        INSERT INTO hgvs_index VALUES ('urn:mavedb:1-a-1','urn:mavedb:1-a-1#1','c.8168a>g','p.asp2723his',NULL);
        INSERT INTO hgvs_index VALUES ('urn:mavedb:2-a-1','urn:mavedb:2-a-1#1',NULL,'p.asp2723his',NULL);
        INSERT INTO mapped_variant VALUES ('urn:mavedb:1-a-1#1','urn:mavedb:1-a-1','ga4gh:VA.brca',NULL,'NC_000017.11:g.1A>G',NULL,NULL);
        INSERT INTO mapped_variant VALUES ('urn:mavedb:2-a-1#1','urn:mavedb:2-a-1','ga4gh:VA.tp53',NULL,NULL,NULL,NULL);
        """
    )
    return MirrorRepository(con)


def test_resolve_hgvs_scoped_by_gene(repo: MirrorRepository) -> None:
    rows = repo.resolve_hgvs("p.asp2723his", gene="BRCA1")
    assert [(r["variant_urn"], r["vrs_id"]) for r in rows] == [
        ("urn:mavedb:1-a-1#1", "ga4gh:VA.brca")
    ]


def test_resolve_hgvs_unscoped_spans_genes(repo: MirrorRepository) -> None:
    vrs = sorted({r["vrs_id"] for r in repo.resolve_hgvs("p.asp2723his")})
    assert vrs == ["ga4gh:VA.brca", "ga4gh:VA.tp53"]


def test_resolve_hgvs_genomic_postmapped(repo: MirrorRepository) -> None:
    rows = repo.resolve_hgvs("nc_000017.11:g.1a>g")
    assert [r["vrs_id"] for r in rows] == ["ga4gh:VA.brca"]


def test_gene_identity(repo: MirrorRepository) -> None:
    assert repo.gene_identity("brca1") == {"symbol": "BRCA1", "organism": "Homo sapiens"}
    assert repo.gene_identity("nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_repository_hgvs.py -v`
Expected: FAIL — `resolve_hgvs` / `gene_identity` not defined.

- [ ] **Step 3: Implement the two methods**

In `mavedb_link/data/repository.py`, add to the `# --- gene + search` section (after `gene_score_sets`, line 171):

```python
    def gene_identity(self, symbol: str) -> dict[str, Any] | None:
        """Thin gene identity from the index (symbol + organism), or None if absent."""
        row = self._con.execute(
            "SELECT gene_symbol, organism FROM gene_index WHERE gene_symbol_upper = ? LIMIT 1",
            (symbol.strip().upper(),),
        ).fetchone()
        if row is None:
            return None
        out: dict[str, Any] = {"symbol": row["gene_symbol"]}
        if row["organism"]:
            out["organism"] = row["organism"]
        return out

    def resolve_hgvs(
        self, hgvs_core_value: str, *, gene: str | None = None
    ) -> list[dict[str, Any]]:
        """Resolve a normalised HGVS core to (variant_urn, score_set_urn, vrs_id) rows.

        Two paths, unioned: target-relative HGVS via ``hgvs_index`` (scoped by
        ``gene`` when given), and genomic/accessioned HGVS via the post-mapped HGVS
        columns on ``mapped_variant`` (case-insensitive). ``hgvs_core_value`` is the
        caller's :func:`scores.hgvs_core` of the input.
        """
        value = hgvs_core_value.strip()
        if not value:
            return []
        out: dict[tuple[str | None, str | None], dict[str, Any]] = {}
        # Target-relative: hgvs_index -> mapped_variant for the VRS.
        if gene and gene.strip():
            sql = (
                "SELECT h.variant_urn, h.score_set_urn, m.vrs_id FROM hgvs_index h "
                "JOIN gene_index g ON g.score_set_urn = h.score_set_urn "
                "LEFT JOIN mapped_variant m ON m.variant_urn = h.variant_urn "
                "WHERE g.gene_symbol_upper = ? AND (h.hgvs_nt = ? OR h.hgvs_pro = ? "
                "OR h.hgvs_splice = ?)"
            )
            params: tuple[Any, ...] = (gene.strip().upper(), value, value, value)
        else:
            sql = (
                "SELECT h.variant_urn, h.score_set_urn, m.vrs_id FROM hgvs_index h "
                "LEFT JOIN mapped_variant m ON m.variant_urn = h.variant_urn "
                "WHERE h.hgvs_nt = ? OR h.hgvs_pro = ? OR h.hgvs_splice = ?"
            )
            params = (value, value, value)
        for r in self._con.execute(sql, params).fetchall():
            out[(r["variant_urn"], r["vrs_id"])] = dict(r)
        # Genomic/accessioned: match the post-mapped HGVS columns directly.
        for r in self._con.execute(
            "SELECT variant_urn, score_set_urn, vrs_id FROM mapped_variant WHERE "
            "post_mapped_hgvs_g = ? COLLATE NOCASE OR post_mapped_hgvs_c = ? COLLATE NOCASE "
            "OR post_mapped_hgvs_p = ? COLLATE NOCASE",
            (value, value, value),
        ).fetchall():
            out.setdefault((r["variant_urn"], r["vrs_id"]), dict(r))
        return sorted(out.values(), key=lambda d: (d.get("score_set_urn") or "", d.get("variant_urn") or ""))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_repository_hgvs.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add mavedb_link/data/repository.py tests/unit/test_repository_hgvs.py
git commit -m "feat(mirror): repository resolve_hgvs + gene_identity"
```

---

### Task 3: HybridClient — `vrs_for_hgvs` + `gene_identity`

**Files:**
- Modify: `mavedb_link/data/hybrid.py` (two duck-typed methods + docstring update)
- Test: `tests/unit/test_hybrid.py` (extend)

**Interfaces:**
- Consumes: `MirrorRepository.resolve_hgvs`, `MirrorRepository.gene_identity` (Task 2); `provenance.record`.
- Produces:
  - `HybridClient.vrs_for_hgvs(hgvs_core_value: str, *, gene: str | None = None) -> list[dict[str, Any]]` (records `mirror` provenance when it returns rows; `[]` on miss → caller goes live).
  - `HybridClient.gene_identity(symbol: str) -> dict[str, Any] | None` (records `mirror` provenance on hit).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_hybrid.py  (append)
def test_vrs_for_hgvs_uses_mirror(hybrid_client) -> None:
    # hybrid_client fixture wraps a built fixture mirror (see existing tests).
    rows = hybrid_client.vrs_for_hgvs("p.asp2723his", gene="BRCA1")
    assert isinstance(rows, list)


def test_gene_identity_thin_from_mirror(hybrid_client) -> None:
    ident = hybrid_client.gene_identity("BRCA1")
    assert ident is None or ident["symbol"].upper() == "BRCA1"
```

(Use the existing hybrid fixture in `tests/unit/test_hybrid.py`; if it builds from the bundled fixture dump, BRCA1 may or may not be present — the asserts tolerate both, the point is the methods exist and return the right *type*. Add a precise-value test only if the fixture is known to contain BRCA1.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_hybrid.py -k "hgvs or gene_identity" -v`
Expected: FAIL — methods not defined.

- [ ] **Step 3: Implement the methods**

In `mavedb_link/data/hybrid.py`, after `mapped_vrs_for_variant` (line 109):

```python
    def vrs_for_hgvs(
        self, hgvs_core_value: str, *, gene: str | None = None
    ) -> list[dict[str, Any]]:
        """Resolve a normalised HGVS core to mapped-variant rows from the mirror.

        Lets find_variant(hgvs=) resolve VRS without probing the live API. Returns
        ``[]`` on miss (no mirror coverage) so the caller falls through to the live
        probe. Records mirror provenance only when it actually answers.
        """
        rows = self._repo.resolve_hgvs(hgvs_core_value, gene=gene)
        if rows:
            provenance.record("mirror", mirror_as_of=self._mirror_as_of)
        return rows

    def gene_identity(self, symbol: str) -> dict[str, Any] | None:
        """Thin gene identity (symbol + organism) from the mirror index, or None."""
        ident = self._repo.gene_identity(symbol)
        if ident is not None:
            provenance.record("mirror", mirror_as_of=self._mirror_as_of)
        return ident
```

Update the module docstring (lines 9-16) to mention the new mirror-served reads (`hgvs_index` resolution + thin gene identity).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_hybrid.py -k "hgvs or gene_identity" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mavedb_link/data/hybrid.py tests/unit/test_hybrid.py
git commit -m "feat(mirror): hybrid vrs_for_hgvs + gene_identity passthrough"
```

---

### Task 4: Service-plane HGVS resolution (`resolvers.py`)

**Files:**
- Modify: `mavedb_link/constants.py` (add `HGVS_PROBE_CAP`)
- Modify: `mavedb_link/services/resolvers.py` (resolver + find_variant signature)
- Test: `tests/unit/test_resolvers_hgvs.py` (new)

**Interfaces:**
- Consumes: `HybridClient.vrs_for_hgvs` (Task 3, duck-typed); `variant_lookup.get_variant_score`; `scores.hgvs_core`; `MAX_GENE_LIMIT`, `HGVS_PROBE_CAP`.
- Produces: `resolvers.find_variant(...)` gains `hgvs: str | None`, `gene: str | None`; returns extra keys `resolved_vrs: list[str]`, `hgvs_input: str | None`, `probe_truncated: bool` (only when hgvs path used). `resolved_by` gains `"hgvs"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_resolvers_hgvs.py
"""find_variant resolves a bare HGVS string via the mirror, then the live probe."""
from __future__ import annotations

from typing import Any

import pytest

from mavedb_link.exceptions import AmbiguousQueryError, InvalidInputError, NotFoundError
from mavedb_link.services import resolvers


class _MirrorClient:
    """Stub HybridClient: mirror resolves hgvs, get_json answers the VRS rollup."""

    def __init__(self, hgvs_rows: list[dict[str, Any]]) -> None:
        self._hgvs_rows = hgvs_rows

    def vrs_for_hgvs(self, value: str, *, gene: str | None = None) -> list[dict[str, Any]]:
        return self._hgvs_rows

    async def get_json(self, path: str, *, params: Any = None) -> Any:
        # VRS rollup: one hit in one score set.
        return [{"variantUrn": "urn:mavedb:1-a-1#1", "postMapped": {"id": "ga4gh:VA.x"},
                 "current": True}]


@pytest.mark.asyncio
async def test_find_variant_by_hgvs_mirror(monkeypatch) -> None:
    client = _MirrorClient([{"variant_urn": "urn:mavedb:1-a-1#1",
                             "score_set_urn": "urn:mavedb:1-a-1", "vrs_id": "ga4gh:VA.x"}])
    out = await resolvers.find_variant(client, hgvs="p.Asp2723His", gene="BRCA1", enrich=False)
    assert out["resolved_by"] == "hgvs"
    assert out["resolved_vrs"] == ["ga4gh:VA.x"]
    assert out["hgvs_input"] == "p.Asp2723His"
    assert out["probe_truncated"] is False
    assert out["hits"] and out["hits"][0]["vrs_id"] == "ga4gh:VA.x"


@pytest.mark.asyncio
async def test_find_variant_hgvs_ambiguous_without_gene() -> None:
    client = _MirrorClient([
        {"variant_urn": "urn:mavedb:1-a-1#1", "score_set_urn": "urn:mavedb:1-a-1", "vrs_id": "ga4gh:VA.a"},
        {"variant_urn": "urn:mavedb:2-a-1#1", "score_set_urn": "urn:mavedb:2-a-1", "vrs_id": "ga4gh:VA.b"},
    ])
    with pytest.raises(AmbiguousQueryError):
        await resolvers.find_variant(client, hgvs="p.Asp2723His", enrich=False)


@pytest.mark.asyncio
async def test_find_variant_hgvs_miss_requires_gene_for_live_probe() -> None:
    client = _MirrorClient([])  # mirror miss
    with pytest.raises(InvalidInputError):
        await resolvers.find_variant(client, hgvs="p.Asp2723His", enrich=False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_resolvers_hgvs.py -v`
Expected: FAIL — `find_variant` has no `hgvs`/`gene` kwargs.

- [ ] **Step 3a: Add the probe cap constant**

In `mavedb_link/constants.py` after `DEFAULT_FIND_LIMIT` (line 69):

```python
#: Max score sets the live HGVS-resolution fallback probes before truncating
#: (one get_variant_score per set). The mirror serves the common case; this caps
#: the live-miss path so a popular gene cannot fan out unboundedly.
HGVS_PROBE_CAP = 10
```

- [ ] **Step 3b: Implement the HGVS resolver in `resolvers.py`**

Add imports at the top of `mavedb_link/services/resolvers.py`:

```python
from mavedb_link.constants import (
    DEFAULT_CLASSIFIED_LIMIT,
    DEFAULT_FIND_LIMIT,
    FUNCTIONAL_CLASSES,
    HGVS_PROBE_CAP,
    MAX_CLASSIFIED_LIMIT,
    MAX_FIND_LIMIT,
    MAX_GENE_LIMIT,
)
from mavedb_link.exceptions import AmbiguousQueryError, InvalidInputError, NotFoundError
from mavedb_link.services import variant_lookup
from mavedb_link.services.scores import hgvs_core
```

Add the resolver functions before `find_variant` (after `_resolve_cross_dataset_ident`, line 159):

```python
def _distinct_vrs(rows: list[dict[str, Any]]) -> list[str]:
    """Distinct, ordered VRS ids from mapped-variant rows (drops unmapped)."""
    return sorted({r["vrs_id"] for r in rows if r.get("vrs_id")})


def _hgvs_candidates(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """{gene-less} candidate descriptors for an ambiguous-HGVS error."""
    return [
        {"score_set_urn": r.get("score_set_urn") or "", "vrs_id": r.get("vrs_id") or ""}
        for r in rows
        if r.get("vrs_id")
    ]


async def _live_probe_hgvs(client: MaveDBClient, hgvs: str, gene: str) -> tuple[list[str], bool]:
    """Resolve HGVS->VRS by probing a gene's score sets live (mirror-miss fallback).

    Replicates the manual probe a caller would otherwise do by hand: list the
    gene's score sets, then get_variant_score(by hgvs) on each (capped at
    HGVS_PROBE_CAP), collecting the genome-mapped VRS of every match. Returns
    ``(distinct_vrs, truncated)``.
    """
    gene_raw = await client.get_json(
        f"/genes/{gene}", params={"limit": MAX_GENE_LIMIT, "offset": 0}
    )
    urns = [
        s.get("urn")
        for s in (gene_raw.get("scoreSets") if isinstance(gene_raw, dict) else None) or []
        if isinstance(s, dict) and s.get("urn")
    ]
    truncated = len(urns) > HGVS_PROBE_CAP
    probes = await asyncio.gather(
        *(
            variant_lookup.get_variant_score(client, urn, hgvs=hgvs, response_mode="standard")
            for urn in urns[:HGVS_PROBE_CAP]
        ),
        return_exceptions=True,
    )
    found: set[str] = set()
    for probe in probes:
        if isinstance(probe, BaseException) or not isinstance(probe, dict):
            continue
        for variant in probe.get("variants") or []:
            for mapping in variant.get("mapped_variants") or []:
                vrs = mapping.get("vrs_id")
                if vrs:
                    found.add(str(vrs))
    if not found:
        raise NotFoundError(
            f"No variant matching HGVS '{hgvs}' was found (with a genome-mapped VRS) "
            f"in the first {min(len(urns), HGVS_PROBE_CAP)} score set(s) for {gene}. "
            "Confirm the HGVS spelling, or call get_gene_score_sets(symbol) and probe "
            "get_variant_score(urn, hgvs=) directly."
        )
    return sorted(found), truncated


async def _vrs_from_hgvs(
    client: MaveDBClient, hgvs: str, gene: str | None
) -> tuple[list[str], bool]:
    """Resolve an HGVS string to VRS id(s): mirror first, then a capped live probe.

    Returns ``(vrs_ids, probe_truncated)``. Raises AmbiguousQueryError when the
    mirror finds the variant in multiple genes and no ``gene`` was given;
    InvalidInputError when a mirror miss needs ``gene`` for the live probe.
    """
    candidate = hgvs.strip()
    if not candidate:
        raise InvalidInputError(
            "Provide an HGVS string (e.g. 'p.Asp2723His' or 'NM_000059.4:c.8167G>A').",
            field="hgvs",
        )
    core = hgvs_core(candidate)
    from_mirror = getattr(client, "vrs_for_hgvs", None)
    if callable(from_mirror):
        rows = from_mirror(core, gene=gene)
        vrs = _distinct_vrs(rows)
        if vrs:
            if len(vrs) > 1 and not (gene and gene.strip()):
                raise AmbiguousQueryError(
                    f"HGVS '{candidate}' maps to {len(vrs)} distinct variants across "
                    "score sets. Re-run with gene= to disambiguate.",
                    candidates=_hgvs_candidates(rows),
                )
            return vrs, False
    if not (gene and gene.strip()):
        raise InvalidInputError(
            f"HGVS '{candidate}' is not in the local mirror; resolving it live needs "
            "gene= (to scope which score sets to probe).",
            field="gene",
            hint="Pass gene='BRCA1' (the HGNC symbol the variant belongs to).",
        )
    return await _live_probe_hgvs(client, candidate, gene.strip())
```

- [ ] **Step 3c: Rework `find_variant` to accept HGVS + multiple VRS**

Replace `find_variant` (lines 162-207) with:

```python
async def find_variant(
    client: MaveDBClient,
    vrs_id: str | None = None,
    *,
    variant_urn: str | None = None,
    hgvs: str | None = None,
    gene: str | None = None,
    only_current: bool = True,
    enrich: bool = True,
    limit: int = DEFAULT_FIND_LIMIT,
    offset: int = 0,
    response_mode: str = "compact",
) -> dict[str, Any]:
    """Find one variant across every MaveDB score set (cross-dataset rollup).

    Anchor on a GA4GH VRS allele id, a ``variant_urn`` (resolved to its VRS via the
    variant record), OR a bare ``hgvs`` string (resolved via the mirror's hgvs_index,
    falling back to a capped live probe of ``gene``'s score sets). With ``enrich``
    (default) each hit also carries the variant's ``score`` + calibrated
    ``classifications``.
    """
    extra: dict[str, Any] = {}
    if hgvs and hgvs.strip():
        idents, truncated = await _vrs_from_hgvs(client, hgvs, gene)
        resolved_by = "hgvs"
        extra = {"hgvs_input": hgvs.strip(), "probe_truncated": truncated}
    else:
        ident, resolved_by = await _resolve_cross_dataset_ident(client, vrs_id, variant_urn)
        idents = [ident]
    capped = _clamp(limit, 1, MAX_FIND_LIMIT)
    merged: dict[str, Any] = {}
    for ident in idents:
        raw = await client.get_json(
            f"/mapped-variants/vrs/{quote(ident, safe='')}",
            params={"only_current": only_current},
        )
        rows = raw if isinstance(raw, list) else (raw.get("mappedVariants") or [])
        for row in rows:
            merged.setdefault(_mapped_variant_urn(row) or id(row), row)
    items = sorted(merged.values(), key=_cross_dataset_sort_key)
    total = len(items)
    page = items[offset : offset + capped]
    hits: list[dict[str, Any]] = []
    for row in page:
        hit = shape_mapped_variant(row, response_mode)
        variant_urn_hit = hit.get("variant_urn")
        hit["score_set_urn"] = (
            score_set_urn_of_variant(variant_urn_hit) if variant_urn_hit else None
        )
        hits.append(hit)
    if enrich:
        await asyncio.gather(*(_enrich_hit(client, h) for h in hits))
    return {
        "vrs_id": idents[0],
        "resolved_vrs": idents,
        "resolved_by": resolved_by,
        "hits": hits,
        "enriched": enrich,
        **extra,
        **_page_block(total=total, returned=len(hits), limit=capped, offset=offset),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_resolvers_hgvs.py -v`
Expected: PASS (3 tests). Then run the existing resolver tests to confirm no regression:
Run: `uv run pytest tests/unit -k "resolver or find_variant" -v`
Expected: PASS (existing `vrs_id`/`variant_urn` paths still return `resolved_vrs`/`vrs_id` correctly — update any existing assertion that froze the exact key set to include `resolved_vrs`).

- [ ] **Step 5: Commit**

```bash
git add mavedb_link/constants.py mavedb_link/services/resolvers.py tests/unit/test_resolvers_hgvs.py
git commit -m "feat(find): HGVS-first resolution (mirror + capped live probe)"
```

---

### Task 5: Wire HGVS through the service + MCP tool + schema

**Files:**
- Modify: `mavedb_link/services/mavedb_service.py:481-502` (`find_variant` delegate)
- Modify: `mavedb_link/mcp/tools/resolvers.py` (params, description, context)
- Modify: `mavedb_link/mcp/schemas.py:172-178` (`FIND_VARIANT_SCHEMA`)
- Test: `tests/unit/test_tools_find_variant.py` (extend or new)

**Interfaces:**
- Consumes: `resolvers.find_variant(..., hgvs=, gene=)` (Task 4).
- Produces: the `find_variant` MCP tool accepts `hgvs` + `gene`; output schema declares `resolved_vrs`, `hgvs_input`, `probe_truncated`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_tools_find_variant.py  (new or append)
"""The find_variant MCP tool exposes hgvs/gene and stays Tool-Naming compliant."""
from __future__ import annotations

import inspect

from mavedb_link.services import mavedb_service


def test_service_find_variant_accepts_hgvs_and_gene() -> None:
    sig = inspect.signature(mavedb_service.MaveDBService.find_variant)
    assert "hgvs" in sig.parameters
    assert "gene" in sig.parameters
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_tools_find_variant.py -v`
Expected: FAIL — service `find_variant` lacks `hgvs`/`gene`.

- [ ] **Step 3a: Extend the service delegate**

In `mavedb_link/services/mavedb_service.py`, replace the `find_variant` method (lines 481-502):

```python
    async def find_variant(
        self,
        vrs_id: str | None = None,
        *,
        variant_urn: str | None = None,
        hgvs: str | None = None,
        gene: str | None = None,
        only_current: bool = True,
        enrich: bool = True,
        limit: int = DEFAULT_FIND_LIMIT,
        offset: int = 0,
        response_mode: str = shaping.DEFAULT_RESPONSE_MODE,
    ) -> dict[str, Any]:
        """Find a variant across every score set by VRS id, variant URN, or HGVS (delegated)."""
        return await resolvers.find_variant(
            self._client,
            vrs_id,
            variant_urn=variant_urn,
            hgvs=hgvs,
            gene=gene,
            only_current=only_current,
            enrich=enrich,
            limit=limit,
            offset=offset,
            response_mode=response_mode,
        )
```

- [ ] **Step 3b: Add the tool params + description**

In `mavedb_link/mcp/tools/resolvers.py`, add annotated types after `_VariantUrn` (line 52):

```python
_Hgvs = Annotated[
    str | None,
    Field(
        default=None,
        description="A bare HGVS string (e.g. 'p.Asp2723His', 'c.8167G>A', or an "
        "accessioned 'NM_000059.4:c.8167G>A'). Resolved to its VRS internally via the "
        "local mirror, falling back to a capped live probe of gene='s score sets — so "
        "you do NOT pre-map it. Pass gene= alongside to disambiguate / enable the live "
        "fallback. Use this OR vrs_id OR variant_urn.",
        examples=["p.Asp2723His", "NM_000059.4:c.8167G>A"],
    ),
]
_Gene = Annotated[
    str | None,
    Field(
        default=None,
        description="HGNC gene symbol that scopes an hgvs= lookup (required when the "
        "HGVS is not in the mirror and must be resolved live). Ignored unless hgvs= is set.",
        examples=["BRCA1", "TP53"],
    ),
]
```

Update the tool's `description=` to mention the HGVS entry (append before the `Signature:` sentence) and update the signature sentence:

```python
            "ALSO accepts a bare hgvs= string (+ optional gene=) resolved to its VRS "
            "internally — chain straight from an HGVS the user typed, no map-first "
            "round-trip. ClinGen Allele IDs are not accepted upstream; pass the "
            "variant_urn instead. Paged via offset/limit. Signature: find_variant("
            "vrs_id=, variant_urn=, hgvs=, gene=, only_current=, enrich=, limit=, "
            "offset=, response_mode=)."
```

Add the params to the tool function signature (after `variant_urn`):

```python
    async def find_variant(
        vrs_id: _VrsId = None,
        variant_urn: _VariantUrn = None,
        hgvs: _Hgvs = None,
        gene: _Gene = None,
        only_current: Annotated[
            bool, Field(description="Keep only current genome mappings (default true).")
        ] = True,
        enrich: Annotated[
            bool, Field(description="Attach each hit's score + classifications (default true).")
        ] = True,
        limit: _FindLimit = DEFAULT_FIND_LIMIT,
        offset: _Offset = 0,
        response_mode: ResponseMode = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await get_mavedb_service().find_variant(
                vrs_id,
                variant_urn=variant_urn,
                hgvs=hgvs,
                gene=gene,
                only_current=only_current,
                enrich=enrich,
                limit=limit,
                offset=offset,
                response_mode=response_mode,
            )
            payload.setdefault("_meta", {})["next_commands"] = after_find_variant(payload)
            return payload

        return await run_mcp_tool(
            "find_variant",
            call,
            context=McpErrorContext(
                "find_variant",
                arguments={
                    "vrs_id": vrs_id,
                    "variant_urn": variant_urn,
                    "hgvs": hgvs,
                    "gene": gene,
                },
                response_mode=response_mode,
            ),
        )
```

- [ ] **Step 3c: Extend the output schema**

In `mavedb_link/mcp/schemas.py` replace `FIND_VARIANT_SCHEMA` (lines 172-178):

```python
FIND_VARIANT_SCHEMA = _envelope(
    vrs_id=_STR,  # the resolved GA4GH allele id (first, when several resolved)
    resolved_vrs=_ARR,  # all resolved GA4GH allele ids (>=1)
    resolved_by=_STR,  # "vrs_id" | "variant_urn" | "hgvs"
    hgvs_input=_STR_NULL,  # the HGVS string that was resolved (hgvs path only)
    probe_truncated=_BOOL,  # live-probe hit its score-set cap (hgvs path only)
    hits=_ARR,  # each: {score_set_urn, variant_urn, vrs_id, clingen_allele_id, score?, classifications?}
    enriched=_BOOL,
    **_PAGE,
)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_tools_find_variant.py tests/unit/test_tool_names.py -v`
Expected: PASS — service accepts hgvs/gene; tool-naming/`TOOLS` list still equals the registered set (no new tool added).

- [ ] **Step 5: Commit**

```bash
git add mavedb_link/services/mavedb_service.py mavedb_link/mcp/tools/resolvers.py mavedb_link/mcp/schemas.py tests/unit/test_tools_find_variant.py
git commit -m "feat(find): wire hgvs/gene through service, tool, and output schema"
```

---

### Task 6: VRS re-tier — lean `standard` in `shape_mapped_variant`

**Files:**
- Modify: `mavedb_link/services/shaping.py:301-325` (+ a `_summarize_vrs` helper)
- Test: `tests/unit/test_shaping_vrs_tier.py` (new)

**Interfaces:**
- Produces: `shape_mapped_variant(raw, "standard")` returns a flat `post_mapped` summary (`{assembly, sequence_id, start, end, ref, alt}` — keys present only when parseable), drops `pre_mapped`, keeps `vrs_version`/`alignment_level`; `"full"` unchanged (full `pre_mapped` + `post_mapped`); `compact`/`minimal` unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_shaping_vrs_tier.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_shaping_vrs_tier.py -v`
Expected: FAIL — standard currently emits full `pre_mapped` + nested `post_mapped`.

- [ ] **Step 3: Implement `_summarize_vrs` + re-tier**

In `mavedb_link/services/shaping.py`, add a helper above `shape_mapped_variant` (line 301):

```python
def _summarize_vrs(post: dict[str, Any]) -> dict[str, Any]:
    """Flatten a post-mapped VRS allele to its genomic coordinates (defensive).

    Tolerates VRS 1.x/2.x shape differences and returns only the keys it can parse
    (never raises). Keys: assembly, sequence_id, start, end, ref, alt.
    """
    if not isinstance(post, dict):
        return {}
    loc = post.get("location") or {}
    ref_seq = loc.get("sequenceReference") or {}
    state = post.get("state") or {}
    summary: dict[str, Any] = {
        "assembly": ref_seq.get("assembly"),
        "sequence_id": ref_seq.get("refgetAccession") or loc.get("sequence_id"),
        "start": (loc.get("start") if "start" in loc else (loc.get("interval") or {}).get("start")),
        "end": (loc.get("end") if "end" in loc else (loc.get("interval") or {}).get("end")),
        "ref": state.get("referenceSequence"),
        "alt": state.get("sequence"),
    }
    return {k: v for k, v in summary.items() if v is not None}
```

Then change the `standard`/`full` branch of `shape_mapped_variant` (lines 314-324):

```python
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
        summary = _summarize_vrs(post if isinstance(post, dict) else {})
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_shaping_vrs_tier.py -v`
Expected: PASS (3 tests). Then regression-check the variant shapers:
Run: `uv run pytest tests/unit -k "shap or mapped_variant or single_variant" -v`
Expected: PASS — update any existing test that asserted `pre_mapped` at `standard` to use `full` (this is the intended surface change).

- [ ] **Step 5: Commit**

```bash
git add mavedb_link/services/shaping.py tests/unit/test_shaping_vrs_tier.py
git commit -m "feat(shaping): lean standard VRS summary; full keeps full objects"
```

---

### Task 7: Mirror the gene hop — cached + time-boxed identity

**Files:**
- Modify: `mavedb_link/constants.py` (`GENE_IDENTITY_TIMEOUT_S`, `GENE_IDENTITY_CACHE_MAX`)
- Modify: `mavedb_link/services/resolvers.py` (gene-identity cache + `resolve_gene_identity`)
- Modify: `mavedb_link/services/mavedb_service.py:212-281` (`get_gene_score_sets`)
- Modify: `tests/conftest.py` (autouse: clear the gene-identity cache)
- Test: `tests/unit/test_gene_identity.py` (new)

**Interfaces:**
- Consumes: `HybridClient.gene_identity` (Task 3, duck-typed).
- Produces: `resolvers.resolve_gene_identity(client, symbol) -> tuple[dict[str, Any], str]` returning `(gene_raw_or_thin, source)` where `source ∈ {"live", "cache", "mirror"}`; `resolvers.clear_gene_identity_cache() -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_gene_identity.py
"""resolve_gene_identity: cache hit, live, and degrade-to-mirror on timeout."""
from __future__ import annotations

import asyncio

import pytest

from mavedb_link.services import resolvers


class _SlowMirrorClient:
    def __init__(self, *, slow: bool) -> None:
        self._slow = slow

    def gene_identity(self, symbol: str) -> dict | None:
        return {"symbol": symbol, "organism": "Homo sapiens"}

    async def get_json(self, path: str, *, params=None):
        if self._slow:
            await asyncio.sleep(10)  # exceeds the timeout -> degrade
        return {"symbol": "BRCA1", "name": "BRCA1 DNA repair associated", "hgncId": "HGNC:1100",
                "scoreSets": [{"urn": "urn:mavedb:1-a-1"}]}


@pytest.fixture(autouse=True)
def _clear() -> None:
    resolvers.clear_gene_identity_cache()


@pytest.mark.asyncio
async def test_live_then_cache() -> None:
    client = _SlowMirrorClient(slow=False)
    raw, source = await resolvers.resolve_gene_identity(client, "BRCA1")
    assert raw["hgncId"] == "HGNC:1100" and source == "live"
    raw2, source2 = await resolvers.resolve_gene_identity(client, "BRCA1")
    assert source2 == "cache" and raw2["hgncId"] == "HGNC:1100"


@pytest.mark.asyncio
async def test_timeout_degrades_to_mirror(monkeypatch) -> None:
    monkeypatch.setattr(resolvers, "GENE_IDENTITY_TIMEOUT_S", 0.05)
    client = _SlowMirrorClient(slow=True)
    raw, source = await resolvers.resolve_gene_identity(client, "BRCA1")
    assert source == "mirror" and raw == {"symbol": "BRCA1", "organism": "Homo sapiens"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_gene_identity.py -v`
Expected: FAIL — `resolve_gene_identity` / `clear_gene_identity_cache` not defined.

- [ ] **Step 3a: Add constants**

In `mavedb_link/constants.py` (near the other limits, after `DEFAULT_GENE_LIMIT` line 67):

```python
#: Degradation guard for the live /genes identity fetch behind get_gene_score_sets.
#: Bounds the worst case before falling back to mirror-derived thin identity; the
#: score-set listing is served from the mirror regardless, so this gates identity only.
GENE_IDENTITY_TIMEOUT_S = 5.0
#: Bounded FIFO size for the process-wide /genes identity memo.
GENE_IDENTITY_CACHE_MAX = 512
```

- [ ] **Step 3b: Add the cache + resolver to `resolvers.py`**

Add to the imports of `mavedb_link/services/resolvers.py`:

```python
from mavedb_link.constants import (
    ...,
    GENE_IDENTITY_CACHE_MAX,
    GENE_IDENTITY_TIMEOUT_S,
    ...,
)
```

Add near the HGVS cache (after line 46):

```python
#: Process-wide memo of /genes identity (rich HGNC fields post-date the dump, so
#: it is fetched live; idempotent within a snapshot window). Bounded FIFO.
_GENE_IDENTITY_CACHE: dict[str, dict[str, Any]] = {}


def clear_gene_identity_cache() -> None:
    """Drop the gene-identity memo (used for test isolation)."""
    _GENE_IDENTITY_CACHE.clear()


async def resolve_gene_identity(
    client: MaveDBClient, symbol: str
) -> tuple[dict[str, Any], str]:
    """Return ``(gene_record, source)`` for a symbol: cache | live | mirror-thin.

    Rich HGNC identity is fetched live but process-cached and time-boxed: a cache
    hit is instant; a slow/failed live fetch degrades to the mirror's thin identity
    (symbol + organism) when the mirror knows the gene, else the error propagates.
    """
    sym = symbol.strip()
    cached = _GENE_IDENTITY_CACHE.get(sym)
    if cached is not None:
        return dict(cached), "cache"
    thin_fn = getattr(client, "gene_identity", None)
    thin = thin_fn(sym) if callable(thin_fn) else None
    try:
        raw = await asyncio.wait_for(
            client.get_json(f"/genes/{sym}", params={"limit": MAX_GENE_LIMIT, "offset": 0}),
            timeout=GENE_IDENTITY_TIMEOUT_S,
        )
    except (TimeoutError, Exception) as exc:  # degrade only if the mirror knows it
        if thin is not None:
            return dict(thin), "mirror"
        raise exc if isinstance(exc, Exception) else NotFoundError(f"Unknown gene '{sym}'.")
    if isinstance(raw, dict):
        if len(_GENE_IDENTITY_CACHE) >= GENE_IDENTITY_CACHE_MAX:
            _GENE_IDENTITY_CACHE.pop(next(iter(_GENE_IDENTITY_CACHE)), None)
        _GENE_IDENTITY_CACHE[sym] = raw
    return raw, "live"
```

> Note: `except (TimeoutError, Exception)` is intentionally broad — `asyncio.wait_for` raises `TimeoutError` (3.12) and we also degrade on any live error when the mirror can answer. `ruff`'s `BLE001` may flag this; add `# noqa: BLE001` if needed and keep the targeted re-raise.

- [ ] **Step 3c: Use it in `get_gene_score_sets`**

In `mavedb_link/services/mavedb_service.py`, rework `get_gene_score_sets` (lines 212-281) to source identity via the resolver and the listing from target-search (mirror), so the listing no longer blocks on live `/genes`:

```python
    async def get_gene_score_sets(
        self,
        symbol: str,
        *,
        limit: int = DEFAULT_GENE_LIMIT,
        offset: int = 0,
        response_mode: str = shaping.DEFAULT_RESPONSE_MODE,
    ) -> dict[str, Any]:
        """Resolve a gene to the COMPLETE set of its published score sets (DEF-1).

        The score-set listing is served from the mirror's target index (instant);
        rich HGNC identity is fetched via the cached/time-boxed resolver, degrading
        to mirror-thin identity rather than blocking. Live identity (when fetched)
        also catches score sets newer than the snapshot via the union.
        """
        capped = _clamp(limit, 1, MAX_GENE_LIMIT)
        sym = symbol.strip()
        identity_task = resolvers.resolve_gene_identity(self._client, sym)
        target_task = self._client.post_json(
            "/score-sets/search",
            json={"published": True, "targets": [sym], "limit": MAX_SEARCH_LIMIT},
        )
        gathered: Any = await asyncio.gather(identity_task, target_task, return_exceptions=True)
        identity_res, target_resp = gathered[0], gathered[1]
        if isinstance(identity_res, BaseException):
            raise identity_res  # gene genuinely unknown to both mirror and live
        gene_raw, identity_source = identity_res
        gene_items, _ = _extract_items(
            gene_raw, ("scoreSets", "score_sets"), ("total", "numScoreSets")
        )
        degraded = isinstance(target_resp, BaseException)
        target_items: list[Any] = []
        if not degraded:
            target_items, _ = _extract_items(
                target_resp, ("scoreSets", "items", "results"), ("numScoreSets", "total", "count")
            )
        merged: dict[str, Any] = {}
        for item in (*gene_items, *target_items):  # gene first: it wins on dedupe
            urn = item.get("urn") if isinstance(item, dict) else None
            if urn:
                merged.setdefault(urn, item)
        ordered = sorted(merged.values(), key=lambda it: it.get("urn") or "")
        total = len(ordered)
        page = ordered[offset : offset + capped]
        results = [shaping.shape_score_set(it, response_mode, listing=True) for it in page]
        coverage: dict[str, Any] = {
            "sources": ["gene_endpoint", "target_search"],
            "gene_endpoint": len(gene_items),
            "target_search": len(target_items),
            "union": total,
            "gene_identity_source": identity_source,
        }
        if degraded:
            coverage["degraded"] = True
        payload: dict[str, Any] = {
            "gene": shaping.shape_gene(gene_raw, response_mode),
            "score_sets": results,
            **_page_block(total=total, returned=len(results), limit=capped, offset=offset),
        }
        if response_mode == "minimal":
            meta = payload.setdefault("_meta", {})
            meta["coverage"] = coverage
            meta["total_scored_variants"] = gene_raw.get("totalScoredVariants")
        else:
            payload["total_scored_variants"] = gene_raw.get("totalScoredVariants")
            payload["coverage"] = coverage
        return payload
```

(`shape_gene` already tolerates a thin `{symbol, organism}` dict — it `.get()`s every field — so mirror-degraded identity projects cleanly. `gene_raw.get("totalScoredVariants")` returns `None` on the thin dict, which the schema allows.)

- [ ] **Step 3d: Clear the cache between tests**

In `tests/conftest.py`, find the autouse fixture that calls `clear_hgvs_validation_cache()` and add the gene-identity clear alongside it. If the import is `from mavedb_link.services.resolvers import clear_hgvs_validation_cache`, extend it:

```python
from mavedb_link.services.resolvers import (
    clear_gene_identity_cache,
    clear_hgvs_validation_cache,
)
```

and inside the fixture body:

```python
    clear_hgvs_validation_cache()
    clear_gene_identity_cache()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_gene_identity.py -v`
Expected: PASS (2 tests). Then the gene-tool regression:
Run: `uv run pytest tests/unit -k "gene" -v`
Expected: PASS — update any existing assertion that froze `coverage` keys to allow `gene_identity_source`.

- [ ] **Step 5: Commit**

```bash
git add mavedb_link/constants.py mavedb_link/services/resolvers.py mavedb_link/services/mavedb_service.py tests/conftest.py tests/unit/test_gene_identity.py
git commit -m "perf(gene): mirror the score-set listing; cache + time-box /genes identity"
```

---

### Task 8: Docs, capabilities, eval baseline, and full gate

**Files:**
- Modify: `mavedb_link/mcp/capabilities.py` (`response_mode_semantics` VRS-trim note; mention `hgvs`)
- Modify: `README.md` / `AGENTS.md` (one-line each: HGVS-first entry; mirror gene listing)
- Modify: `docs/specs/2026-06-20-hgvs-first-vrs-trim-gene-mirror-design.md` (status → Implemented)
- Test: `tests/integration` (live) where applicable; eval baseline regen

**Interfaces:** none new — documentation + gate.

- [ ] **Step 1: Update `response_mode_semantics`**

In `mavedb_link/mcp/capabilities.py` (the `response_mode_semantics` string, ~line 239-252), add a sentence:

```
"For mapped-variant rows, standard now returns a FLAT post_mapped genomic "
"summary (assembly, sequence_id, start, end, ref, alt) and drops pre_mapped; "
"request response_mode=full for the complete pre/post VRS objects. find_variant "
"also accepts a bare hgvs= (+ optional gene=) resolved to VRS internally."
```

- [ ] **Step 2: Update README/AGENTS one-liners**

Add to the tool/feature notes (mirror those already present for mapped-variant enumeration): HGVS-first `find_variant(hgvs=, gene=)` and the mirror-served gene listing with cached identity.

- [ ] **Step 3: Run the full local gate**

Run: `make ci-local`
Expected: PASS — `format-check`, `lint-ci`, `lint-loc` (every touched module < 600 LOC), `mypy --strict`, `test-fast`. Fix any `mypy`/`ruff` findings (notably the broad-except in Task 7 and any new imports). Re-run until green.

- [ ] **Step 4: Regenerate the eval baseline**

Run: `make eval-baseline`
Then: `make eval`
Expected: PASS — the gate confirms the `standard`-mode token reduction (VRS trim) and no error-rate regression. Review the baseline diff and confirm the only payload changes are the intended ones (standard mapped-variant rows leaner; `find_variant` gains `resolved_vrs`; `coverage.gene_identity_source` added). Commit the regenerated baseline.

- [ ] **Step 5: Mark the spec implemented + commit**

```bash
git add mavedb_link/mcp/capabilities.py README.md AGENTS.md docs/specs/2026-06-20-hgvs-first-vrs-trim-gene-mirror-design.md tests/
git commit -m "docs+eval: document HGVS-first/VRS-trim/gene-mirror; regen eval baseline"
```

---

## Self-Review

**1. Spec coverage:**
- §1 HGVS-first `find_variant` → Tasks 1 (index), 2 (repo), 3 (hybrid), 4 (resolver + signature), 5 (service/tool/schema). Ambiguity/error rules → Task 4 tests. ✓
- §2 VRS re-tier → Task 6. ✓
- §3 gene hop (mirror listing + cached/time-boxed identity + `gene_identity_source`) → Task 7. ✓
- §4 schema v1→v2, `hgvs_index`, `mapped_variant` HGVS indexes, repo/hybrid methods, v1 auto-reject → Tasks 1–3 (v1 auto-reject is existing `MirrorRepository.open` behavior, exercised by the version-bump test). ✓
- §5 cross-cutting (capabilities, provenance, token budget, `TOOLS` unchanged) → Tasks 5, 7, 8. ✓
- §6 testing & CI / eval baseline → every task + Task 8. ✓
- §7 risks (normalisation, surface change, bundle rebuild, 600-LOC) → handled in Tasks 1/6/8 and the constraints. ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; commands have expected output. ✓

**3. Type consistency:** `hgvs_core` (Task 1) used in Tasks 1 & 4; `resolve_hgvs(hgvs_core_value, gene=)` (Task 2) called by `vrs_for_hgvs` (Task 3) called by `_vrs_from_hgvs` (Task 4). `resolve_gene_identity -> (dict, str)` (Task 7) consumed in `get_gene_score_sets` (Task 7). `find_variant` adds `hgvs`/`gene` consistently across resolver (4), service (5), tool (5). `_summarize_vrs` (Task 6) self-contained. ✓
```
