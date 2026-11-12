"""Candidate retrieval: vector (pgvector HNSW), keyword (Postgres full text), and RRF fusion.

Every query is filtered *first* by chunker, optional company, and an as-of time, so a question asked
"as of" a date can never see a document published after it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import psycopg

from signaldesk.embed import MODELS, embed_query


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    rank: int
    score: float


def _filters(company_ids: list[int] | None, as_of: datetime | None,
             latest_only: bool = False) -> tuple[str, list]:
    """SQL predicates on chunks `c`. latest_only keeps only the newest filing of each form per company
    as of `as_of` (or now): the latest 10-K, the latest 10-Q and the latest earnings release."""
    sql, params = "", []
    if company_ids is not None:  # [] (unknown ticker) must match nothing, not everything
        sql += " AND c.company_id = ANY(%s)"
        params.append(company_ids)
    if as_of is not None:
        sql += " AND c.published_at <= %s"
        params.append(as_of)
    if latest_only:
        sql += """ AND c.document_id IN (
            SELECT DISTINCT ON (d.company_id, d.form, d.doc_kind) d.id FROM documents d
            WHERE d.published_at <= coalesce(%s::timestamptz, now())
              AND (%s::bigint[] IS NULL OR d.company_id = ANY(%s::bigint[]))
            ORDER BY d.company_id, d.form, d.doc_kind, d.published_at DESC)"""
        params += [as_of, company_ids, company_ids]
    return sql, params


# Words that mean "the newest filing", not "any filing".
RE_LATEST = re.compile(
    r"\b(latest|most recent|recent(ly)?|current(ly)?|this (quarter|year)|last quarter|"
    r"next quarter|now|today|so far)\b", re.I)


def wants_latest(question: str) -> bool:
    return bool(RE_LATEST.search(question))


def vector_search(
    conn: psycopg.Connection,
    query: str | np.ndarray,
    *,
    model: str,
    chunker: str,
    k: int = 50,
    company_ids: list[int] | None = None,
    as_of: datetime | None = None,
    exact: bool = False,
    latest_only: bool = False,
) -> list[Hit]:
    m = MODELS[model]
    qvec = embed_query(m, query) if isinstance(query, str) else query
    fsql, fparams = _filters(company_ids, as_of, latest_only)
    # The cast and the model predicate must match the partial HNSW index in 002_hnsw.sql.
    sql = f"""
        SELECT e.chunk_id, e.vector::vector({m.dim}) <=> %s::vector({m.dim}) AS dist
        FROM embeddings e JOIN chunks c ON c.id = e.chunk_id
        WHERE e.model = %s AND c.chunker = %s {fsql}
        ORDER BY e.vector::vector({m.dim}) <=> %s::vector({m.dim})
        LIMIT %s"""
    params = [qvec, m.key, chunker, *fparams, qvec, k]
    with conn.transaction():
        if exact:
            conn.execute("SET LOCAL enable_indexscan = off")
        else:
            # pgvector >= 0.8: keep scanning the graph when filters discard candidates.
            conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            conn.execute("SET LOCAL hnsw.ef_search = 100")
        rows = conn.execute(sql, params).fetchall()
    return [Hit(cid, i + 1, 1.0 - float(d)) for i, (cid, d) in enumerate(rows)]


def keyword_search(
    conn: psycopg.Connection,
    query: str,
    *,
    chunker: str,
    k: int = 50,
    company_ids: list[int] | None = None,
    as_of: datetime | None = None,
    latest_only: bool = False,
) -> list[Hit]:
    fsql, fparams = _filters(company_ids, as_of, latest_only)
    # OR the terms together: websearch_to_tsquery ANDs them, which is too strict for questions.
    sql = f"""
        WITH q AS (
            SELECT to_tsquery('english', string_agg(quote_literal(lexeme), ' | ')) AS tsq
            FROM unnest(to_tsvector('english', %s)) AS t(lexeme, positions, weights)
        )
        SELECT c.id, ts_rank_cd(c.tsv, q.tsq) AS score
        FROM chunks c, q
        WHERE q.tsq IS NOT NULL AND c.tsv @@ q.tsq AND c.chunker = %s {fsql}
        ORDER BY score DESC, c.id
        LIMIT %s"""
    rows = conn.execute(sql, [query, chunker, *fparams, k]).fetchall()
    return [Hit(cid, i + 1, float(s)) for i, (cid, s) in enumerate(rows)]


def rrf(*ranked: list[Hit], k: int = 60, limit: int = 50) -> list[Hit]:
    """Reciprocal rank fusion: score = Σ 1/(k + rank). Needs no score calibration."""
    scores: dict[int, float] = {}
    for hits in ranked:
        for h in hits:
            scores[h.chunk_id] = scores.get(h.chunk_id, 0.0) + 1.0 / (k + h.rank)
    order = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [Hit(cid, i + 1, s) for i, (cid, s) in enumerate(order)]


def company_ids_for(conn: psycopg.Connection, tickers: list[str]) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM companies WHERE upper(ticker) = ANY(%s)", ([t.upper() for t in tickers],)
    ).fetchall()
    return [r[0] for r in rows]
