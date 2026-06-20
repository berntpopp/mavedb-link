-- Local MaveDB mirror schema. Built from the CC0 Zenodo bulk dump
-- (main.json + per-set CSVs). Records are stored as the upstream camelCase JSON
-- so the existing shapers consume them unchanged; the score/count CSVs are stored
-- verbatim (denamespaced to the live header) for faithful paged reads.

CREATE TABLE meta (
    id                    INTEGER PRIMARY KEY CHECK (id = 1),
    schema_version        INTEGER NOT NULL,
    dump_as_of            TEXT,          -- main.json "asOf"
    zenodo_record         TEXT,          -- e.g. "18511521"
    zenodo_version        TEXT,          -- e.g. "4"
    source_url            TEXT,
    source_md5            TEXT,
    experiment_set_count  INTEGER,
    experiment_count      INTEGER,
    score_set_count       INTEGER,
    mapped_variant_count  INTEGER,
    build_utc             TEXT,
    build_duration_s      REAL
);

CREATE TABLE experiment_set (
    urn          TEXT PRIMARY KEY,
    title        TEXT,
    record_json  TEXT NOT NULL
);

CREATE TABLE experiment (
    urn                 TEXT PRIMARY KEY,
    experiment_set_urn  TEXT,
    title               TEXT,
    short_description   TEXT,
    record_json         TEXT NOT NULL
);
CREATE INDEX idx_experiment_set ON experiment (experiment_set_urn);

CREATE TABLE score_set (
    urn                 TEXT PRIMARY KEY,
    experiment_urn      TEXT,
    experiment_set_urn  TEXT,
    title               TEXT,
    short_description   TEXT,
    license             TEXT,
    num_variants        INTEGER,
    published_date      TEXT,
    has_calibrations    INTEGER NOT NULL DEFAULT 0,
    record_json         TEXT NOT NULL
);
CREATE INDEX idx_score_set_experiment ON score_set (experiment_urn);

-- Per-set CSVs, verbatim but denamespaced to the live header (NULL when absent).
CREATE TABLE score_set_data (
    urn              TEXT PRIMARY KEY,
    scores_csv       TEXT,
    counts_csv       TEXT,
    annotations_csv  TEXT
);

-- Gene symbol -> score set membership (uppercased symbol for case-insensitive lookup).
CREATE TABLE gene_index (
    gene_symbol_upper  TEXT,
    gene_symbol        TEXT,
    score_set_urn      TEXT,
    organism           TEXT,
    category           TEXT
);
CREATE INDEX idx_gene_index ON gene_index (gene_symbol_upper);

-- Cross-dataset mapped-variant identity (from the annotations CSVs).
CREATE TABLE mapped_variant (
    variant_urn         TEXT,
    score_set_urn       TEXT,
    vrs_id              TEXT,
    clingen_allele_id   TEXT,
    post_mapped_hgvs_g  TEXT,
    post_mapped_hgvs_p  TEXT,
    post_mapped_hgvs_c  TEXT
);
CREATE INDEX idx_mapped_vrs ON mapped_variant (vrs_id);
CREATE INDEX idx_mapped_clingen ON mapped_variant (clingen_allele_id);
CREATE INDEX idx_mapped_variant_urn ON mapped_variant (variant_urn);
CREATE INDEX idx_mapped_score_set ON mapped_variant (score_set_urn);

-- Precomputed per-set score distribution (so the summary needs no table scan).
CREATE TABLE score_distribution (
    score_set_urn   TEXT PRIMARY KEY,
    n               INTEGER,
    min             REAL,
    max             REAL,
    mean            REAL,
    histogram_json  TEXT,
    quantiles_json  TEXT
);

-- Full-text search over score sets (title + description + genes + authors).
CREATE VIRTUAL TABLE score_set_fts USING fts5 (
    urn UNINDEXED,
    title,
    short_description,
    genes,
    authors,
    tokenize = 'unicode61'
);
