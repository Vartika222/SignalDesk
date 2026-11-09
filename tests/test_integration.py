"""End-to-end on a throwaway database: fixture filings → sections → chunks → embeddings → search → eval.

Uses TEST_DATABASE_URL (default: a signaldesk_test database in the dev container) and is skipped if
Postgres isn't reachable.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
from datetime import UTC, datetime

import psycopg
import pytest

from signaldesk import db, pipeline, retrieval
from signaldesk.evaluation import Gold, Question, evaluate

pytestmark = [pytest.mark.db, pytest.mark.models]

ADMIN_URL = os.environ.get("DATABASE_URL", "postgresql://signaldesk:signaldesk@localhost:5433/signaldesk")
TEST_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://signaldesk:signaldesk@localhost:5433/signaldesk_test"
)

TENK = (
    b"<html><body><p>TABLE OF CONTENTS</p>"
    b"<table><tr><td>Item 1A.</td><td>Risk Factors</td></tr><tr><td>Item 7.</td><td>MD&A</td></tr></table>"
    b"<p>Item 1A. Risk Factors</p>"
    b"<p>Our credit facility contains a financial covenant requiring a minimum interest coverage ratio. "
    b"A breach of this covenant could accelerate repayment of all outstanding borrowings.</p>"
    b"<p>Supply of specialty resin comes from a single supplier in Ohio.</p>"
    b"<p>Item 7. Management's Discussion and Analysis</p>"
    b"<p>Net sales increased 8% to $2.1 billion, driven by industrial widgets.</p>"
    b"</body></html>"
)

PRESS = (
    b"<html><body><p>Acme Widgets reports third quarter results</p>"
    b"<p>Quarterly revenue was $540 million.</p>"
    b"<p>Business Outlook</p>"
    b"<p>Acme now expects full-year revenue of $2.0 billion to $2.05 billion, "
    b"lowered from its prior outlook of $2.2 billion, reflecting weaker European demand.</p>"
    b"<p>Forward-Looking Statements</p><p>This release contains forward-looking statements.</p>"
    b"</body></html>"
)

T_10K = datetime(2024, 2, 1, 21, 5, tzinfo=UTC)
T_8K = datetime(2024, 10, 24, 20, 15, tzinfo=UTC)


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    try:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute("DROP DATABASE IF EXISTS signaldesk_test")
            admin.execute("CREATE DATABASE signaldesk_test")
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not reachable: {e}")
    db.migrate(TEST_URL)

    data_dir = tmp_path_factory.mktemp("data")
    mp = pytest.MonkeyPatch()
    mp.setattr(pipeline, "settings", dataclasses.replace(pipeline.settings, data_dir=data_dir))

    with db.connect(TEST_URL) as c:
        cid = c.execute(
            "INSERT INTO companies (cik, ticker, name) VALUES ('0000000001', 'ACME', 'Acme Widgets Inc')"
            " RETURNING id"
        ).fetchone()[0]
        for acc, form, kind, raw, ts in [
            ("0000000001-24-000001", "10-K", "primary", TENK, T_10K),
            ("0000000001-24-000002", "8-K", "ex99_1", PRESS, T_8K),
        ]:
            rel = f"raw/{acc}.htm"
            (data_dir / "raw").mkdir(exist_ok=True)
            (data_dir / rel).write_bytes(raw)
            c.execute(
                """INSERT INTO documents (company_id, accession_no, form, doc_kind, file_name, source_url,
                       filed_date, accepted_at, published_at, content_hash, raw_path, raw_bytes)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (cid, acc, form, kind, rel, f"https://example.test/{acc}", ts.date(), ts, ts,
                 hashlib.sha256(raw).hexdigest(), rel, len(raw)),
            )
        c.commit()
        pipeline.parse_documents(c)
        pipeline.chunk_sections(c, "sec256")
        pipeline.embed_chunks(c, "bge-small", "sec256")
        yield c
    mp.undo()


def test_sections_found(conn):
    secs = {r[0] for r in conn.execute("SELECT section FROM sections")}
    assert {"risk_factors", "mdna", "press_release", "outlook", "safe_harbor"} <= secs


def test_chunks_trace_back_to_sections(conn):
    rows = conn.execute(
        "SELECT c.text, s.text, c.char_start, c.char_end FROM chunks c JOIN sections s ON s.id = c.section_id"
    ).fetchall()
    assert rows
    for chunk_text, section_text, a, b in rows:
        assert section_text[a:b] == chunk_text


def test_vector_search_finds_guidance(conn):
    hits = retrieval.vector_search(conn, "Did management lower revenue guidance?", model="bge-small",
                                   chunker="sec256", k=3)
    top = conn.execute("SELECT section FROM chunks WHERE id = %s", (hits[0].chunk_id,)).fetchone()[0]
    assert top == "outlook"


def test_as_of_filter_hides_future_documents(conn):
    before_8k = datetime(2024, 10, 24, 20, 0, tzinfo=UTC)  # 15 minutes before acceptance
    for hits in (
        retrieval.vector_search(conn, "sales and revenue outlook", model="bge-small", chunker="sec256", k=50,
                                as_of=before_8k),
        retrieval.keyword_search(conn, "sales and revenue outlook", chunker="sec256", k=50, as_of=before_8k),
    ):
        ids = [h.chunk_id for h in hits]
        assert ids, "10-K chunks should still be visible"
        latest = conn.execute("SELECT max(published_at) FROM chunks WHERE id = ANY(%s)", (ids,)).fetchone()[0]
        assert latest <= before_8k


def test_keyword_search_exact_terms(conn):
    hits = retrieval.keyword_search(conn, "interest coverage covenant", chunker="sec256", k=3)
    text = conn.execute("SELECT text FROM chunks WHERE id = %s", (hits[0].chunk_id,)).fetchone()[0]
    assert "covenant" in text


def test_hnsw_partial_index_is_used(conn):
    from signaldesk.embed import MODELS, embed_query

    q = embed_query(MODELS["bge-small"], "covenant")
    with conn.transaction():
        conn.execute("SET LOCAL enable_seqscan = off")
        plan = "\n".join(r[0] for r in conn.execute(
            """EXPLAIN SELECT chunk_id FROM embeddings WHERE model = 'bge-small'
               ORDER BY vector::vector(384) <=> %s::vector(384) LIMIT 5""", (q,)))
    assert "embeddings_hnsw_bge_small" in plan


def test_exact_and_hnsw_agree_on_tiny_index(conn):
    kw = dict(model="bge-small", chunker="sec256", k=5)
    a = [h.chunk_id for h in retrieval.vector_search(conn, "single supplier risk", **kw)]
    b = [h.chunk_id for h in retrieval.vector_search(conn, "single supplier risk", exact=True, **kw)]
    assert a == b


def test_evaluate_with_quote_gold(conn):
    q = Question(
        id="t1", split="smoke", text="Did Acme lower its full-year revenue outlook?", labelled_by="test",
        gold=[Gold("0000000001-24-000002", "lowered from its prior outlook of $2.2 billion")],
    )

    def hybrid(question):
        kw = dict(chunker="sec256")
        return [h.chunk_id for h in retrieval.rrf(
            retrieval.vector_search(conn, question.text, model="bge-small", k=20, **kw),
            retrieval.keyword_search(conn, question.text, k=20, **kw))]

    df = evaluate(conn, [q], hybrid)
    assert df.loc[0, "recall@5"] == 1.0
    assert df.loc[0, "mrr"] >= 0.5


def test_batches_are_durable_without_clean_exit():
    """A batch written inside conn.transaction() must be visible to another connection immediately,
    before the writing connection closes. If a killed job could lose committed batches, this fails."""
    with db.connect(TEST_URL) as writer, db.connect(TEST_URL) as reader:
        writer.execute("SELECT 1")  # would open an implicit outer transaction without autocommit
        with writer.transaction():
            writer.execute("INSERT INTO runs (kind, pipeline_config) VALUES ('durability', '{}')")
        n = reader.execute("SELECT count(*) FROM runs WHERE kind = 'durability'").fetchone()[0]
        assert n == 1
