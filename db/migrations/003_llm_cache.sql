-- Every model call is cached by a hash of its full request, so reruns cost nothing and are reproducible.
CREATE TABLE llm_calls (
    key            TEXT PRIMARY KEY,           -- sha256 of the canonical request JSON
    model          TEXT NOT NULL,
    purpose        TEXT NOT NULL,              -- answer | extract | ...
    request        JSONB NOT NULL,
    response       JSONB NOT NULL,
    stop_reason    TEXT,
    input_tokens   INT,
    output_tokens  INT,
    latency_ms     INT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX llm_calls_created ON llm_calls (created_at);
