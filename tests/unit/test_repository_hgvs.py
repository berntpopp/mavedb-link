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
    # The genomic path matches the FULL accessioned form (post_mapped columns keep
    # the accession); the core body alone must NOT match the full stored value.
    rows = repo.resolve_hgvs("g.1a>g", "nc_000017.11:g.1a>g")
    assert [r["vrs_id"] for r in rows] == ["ga4gh:VA.brca"]
    assert repo.resolve_hgvs("g.1a>g") == []  # core-only: no genomic match


def test_gene_identity(repo: MirrorRepository) -> None:
    assert repo.gene_identity("brca1") == {"symbol": "BRCA1", "organism": "Homo sapiens"}
    assert repo.gene_identity("nope") is None


def test_hgvs_variant_urns_scoped_by_gene(repo: MirrorRepository) -> None:
    # The VRS-less hgvs_index lookup (VRS comes from the lazy cache, not the empty
    # mirror mapped_variant): returns the variant URN + its score set, gene-scoped.
    rows = repo.hgvs_variant_urns("p.asp2723his", gene="BRCA1")
    assert [(r["variant_urn"], r["score_set_urn"]) for r in rows] == [
        ("urn:mavedb:1-a-1#1", "urn:mavedb:1-a-1")
    ]


def test_hgvs_variant_urns_unscoped_spans_genes(repo: MirrorRepository) -> None:
    urns = sorted(r["variant_urn"] for r in repo.hgvs_variant_urns("p.asp2723his"))
    assert urns == ["urn:mavedb:1-a-1#1", "urn:mavedb:2-a-1#1"]
    assert repo.hgvs_variant_urns("") == []
