"""Candidate retrieval: vector (pgvector HNSW), keyword (Postgres full text), and RRF fusion.

Every query is filtered *first* by chunker, optional company, and an as-of time, so a question asked
"as of" a date can never see a document published after it.
"""

from __future__ import annotations

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


def _filters(company_ids: list[int] | None, as_of: datetime | None) -> tuple[str, list]:
    sql, params = "", []
    if company_ids:
        sql += " AND c.company_id = ANY(%s)"
        params.append(company_ids)
    if as_of is not None:
        sql += " AND c.published_at <= %s"
        params.append(as_of)
    return sql, params


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
) -> list[Hit]:
    m = MODELS[model]
    qvec = embed_query(m, query) if isinstance(query, str) else query
    fsql, fparams = _filters(company_ids, as_of)
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
) -> list[Hit]:
    fsql, fparams = _filters(company_ids, as_of)
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
