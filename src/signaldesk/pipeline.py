"""Batch steps: parse raw documents → sections, sections → chunks, chunks → embeddings.

Each step is idempotent: it only processes rows that don't yet have output for the given version.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import psycopg

from signaldesk.chunk import CHUNKERS, chunk_text
from signaldesk.config import settings
from signaldesk.embed import MODELS, embed_passages, passage_input
from signaldesk.parse import PARSER_VERSION, parse_document

log = logging.getLogger(__name__)


def parse_documents(conn: psycopg.Connection, parser: str = PARSER_VERSION) -> int:
    docs = conn.execute(
        """SELECT d.id, d.form, d.raw_path FROM documents d
           WHERE NOT EXISTS (SELECT 1 FROM sections s WHERE s.document_id = d.id AND s.parser = %s)
           ORDER BY d.id""",
        (parser,),
    ).fetchall()
    for doc_id, form, raw_path in docs:
        raw = (settings.data_dir / raw_path).read_bytes()
        sections = parse_document(raw, form)
        with conn.transaction(), conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO sections (document_id, parser, section, title, ordinal, text)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                [(doc_id, parser, s.section, s.title, i, s.text) for i, s in enumerate(sections)],
            )
        log.info("parsed document %s (%s): %d sections", doc_id, form, len(sections))
    return len(docs)


def chunk_sections(conn: psycopg.Connection, chunker: str, parser: str = PARSER_VERSION) -> int:
    cfg = CHUNKERS[chunker]
    rows = conn.execute(
        """SELECT s.id, s.document_id, d.company_id, s.section, s.text, d.published_at
           FROM sections s JOIN documents d ON d.id = s.document_id
           WHERE s.parser = %s AND s.section <> 'front_matter'
             AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.section_id = s.id AND c.chunker = %s)
           ORDER BY s.id""",
        (parser, chunker),
    ).fetchall()
    total = 0
    for sid, did, cid, section, text, published in rows:
        chunks = chunk_text(text, cfg)
        with conn.transaction(), conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO chunks (document_id, section_id, company_id, chunker, section,
                       ordinal, text, char_start, char_end, token_count, published_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(did, sid, cid, chunker, section, c.ordinal, c.text, c.char_start, c.char_end,
                  c.token_count, published) for c in chunks],
            )
        total += len(chunks)
    log.info("chunker %s: %d sections → %d chunks", chunker, len(rows), total)
    return total


def _batched(xs: list, n: int) -> Iterable[list]:
    for i in range(0, len(xs), n):
        yield xs[i : i + n]


def embed_chunks(conn: psycopg.Connection, model: str, chunker: str, batch: int = 64) -> int:
    m = MODELS[model]
    rows = conn.execute(
        """SELECT c.id, co.name, d.form, coalesce(s.title, c.section), c.text
           FROM chunks c
           JOIN documents d ON d.id = c.document_id
           JOIN companies co ON co.id = c.company_id
           JOIN sections s ON s.id = c.section_id
           WHERE c.chunker = %s
             AND NOT EXISTS (SELECT 1 FROM embeddings e WHERE e.chunk_id = c.id AND e.model = %s)
           ORDER BY c.id""",
        (chunker, m.key),
    ).fetchall()
    done = 0
    for part in _batched(rows, batch * 8):
        vecs = embed_passages(m, [passage_input(n, f, t, x) for _, n, f, t, x in part], batch)
        with conn.transaction(), conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO embeddings (chunk_id, model, dim, vector) VALUES (%s, %s, %s, %s)",
                [(r[0], m.key, m.dim, v) for r, v in zip(part, vecs, strict=True)],
            )
        done += len(part)
        log.info("embedded %d/%d chunks (%s, %s)", done, len(rows), chunker, model)
    return done
