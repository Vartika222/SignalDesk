-- Phase 0/1 schema. Later phases add signals, prices, rules, backtests in new migrations.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE companies (
    id          BIGSERIAL PRIMARY KEY,
    cik         TEXT NOT NULL UNIQUE,          -- zero-padded to 10 digits
    ticker      TEXT,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per fetched document (a filing can contribute more than one, e.g. 8-K + EX-99.1).
CREATE TABLE documents (
    id               BIGSERIAL PRIMARY KEY,
    company_id       BIGINT NOT NULL REFERENCES companies(id),
    accession_no     TEXT NOT NULL,             -- 0000320193-24-000123
    form             TEXT NOT NULL,             -- 10-K, 10-Q, 8-K
    doc_kind         TEXT NOT NULL,             -- primary | ex99_1
    items            TEXT,                      -- 8-K items, e.g. "2.02,9.01"
    file_name        TEXT NOT NULL,
    source_url       TEXT NOT NULL,
    filed_date       DATE NOT NULL,             -- EDGAR filing date (can be next business day)
    period_of_report DATE,
    accepted_at      TIMESTAMPTZ NOT NULL,      -- from -index.htm "Accepted" (US/Eastern), stored UTC
    accepted_at_json TIMESTAMPTZ,               -- submissions JSON acceptanceDateTime, kept for audit
    published_at     TIMESTAMPTZ NOT NULL,      -- point-in-time key; = accepted_at
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    content_hash     TEXT NOT NULL UNIQUE,      -- sha256 of raw bytes; de-duplication
    raw_path         TEXT NOT NULL,             -- relative to DATA_DIR
    raw_bytes        BIGINT NOT NULL,
    UNIQUE (accession_no, doc_kind)
);
CREATE INDEX documents_company_published ON documents (company_id, published_at);

CREATE TABLE sections (
    id           BIGSERIAL PRIMARY KEY,
    document_id  BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    parser       TEXT NOT NULL,                 -- parser version, so re-parses are comparable
    section      TEXT NOT NULL,                 -- item_1a, item_7, press_release, ...
    title        TEXT,
    ordinal      INT NOT NULL,
    text         TEXT NOT NULL,
    UNIQUE (document_id, parser, ordinal)
);

CREATE TABLE chunks (
    id           BIGSERIAL PRIMARY KEY,
    document_id  BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    section_id   BIGINT NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    company_id   BIGINT NOT NULL REFERENCES companies(id),
    chunker      TEXT NOT NULL,                 -- config name, e.g. sec512
    section      TEXT NOT NULL,
    ordinal      INT NOT NULL,
    text         TEXT NOT NULL,
    char_start   INT NOT NULL,                  -- offsets into sections.text
    char_end     INT NOT NULL,
    token_count  INT NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,          -- copied from documents for cheap as-of filters
    tsv          TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    UNIQUE (section_id, chunker, ordinal)
);
CREATE INDEX chunks_filter ON chunks (chunker, company_id, published_at);
CREATE INDEX chunks_tsv ON chunks USING gin (tsv);

-- One row per chunk per model. The column is dimensionless so models of different sizes can sit
-- side by side; each model gets its own partial HNSW index on a cast expression (see 002).
CREATE TABLE embeddings (
    chunk_id  BIGINT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    model     TEXT NOT NULL,
    dim       INT NOT NULL,
    vector    VECTOR NOT NULL,
    PRIMARY KEY (chunk_id, model)
);

-- Labelled evaluation set. Gold labels are quote spans, not chunk ids, so different chunkers are
-- judged against the same truth.
CREATE TABLE questions (
    id           TEXT PRIMARY KEY,
    split        TEXT NOT NULL CHECK (split IN ('dev', 'heldout', 'smoke')),
    text         TEXT NOT NULL,
    ticker       TEXT,
    as_of        TIMESTAMPTZ,
    gold         JSONB NOT NULL,                -- [{accession_no, quote}]
    gold_answer  TEXT,
    labelled_by  TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE runs (
    id              BIGSERIAL PRIMARY KEY,
    kind            TEXT NOT NULL,              -- retrieval_eval, ...
    pipeline_config JSONB NOT NULL,
    git_sha         TEXT,
    data_snapshot   TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    metrics_json    JSONB
);

-- Postgres-backed queue (claimed with FOR UPDATE SKIP LOCKED).
CREATE TABLE jobs (
    id          BIGSERIAL PRIMARY KEY,
    kind        TEXT NOT NULL,
    payload     JSONB NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
    attempts    INT NOT NULL DEFAULT 0,
    last_error  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX jobs_queued ON jobs (kind, created_at) WHERE status = 'queued';
