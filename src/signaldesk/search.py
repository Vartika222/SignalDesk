"""The full retrieval pipeline used by answers and evaluation.

    filter (company, as-of, optionally latest filings only)
      → vector + keyword candidates → RRF → optional cross-encoder rerank → top k

Every stage is a switch in `SearchConfig`, so notebook 03 can measure what each one adds.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from functools import lru_cache

import psycopg

from signaldesk import retrieval as r

RERANKERS = {"base": "BAAI/bge-reranker-base", "v2-m3": "BAAI/bge-reranker-v2-m3"}


@dataclass(frozen=True)
class SearchConfig:
    chunker: str = "sec256"
    model: str = "bge-base"
    mode: str = "hybrid"  # vector | keyword | hybrid
    candidates: int = 50  # per retriever, before fusion
    rerank: bool = False
    rerank_top: int = 50  # how many fused candidates the cross-encoder rescores
    reranker: str = "base"  # key in RERANKERS
    recency: str = "off"  # off | auto (latest filings when the question asks for them) | always
    k: int = 10

    def label(self) -> str:
        parts = [self.chunker, self.model if self.mode != "keyword" else "-", self.mode]
        if self.recency != "off":
            parts.append(f"recency={self.recency}")
        if self.rerank:
            parts.append(f"rerank[{self.reranker}]@{self.rerank_top}")
        return " · ".join(parts)

    def to_dict(self) -> dict:
        return asdict(self)

    def but(self, **kw) -> SearchConfig:
        return replace(self, **kw)


@lru_cache(maxsize=2)
def _cross_encoder(name: str):
    import torch
    from sentence_transformers import CrossEncoder

    from signaldesk.embed import device

    # Half precision on the GPU: identical scores in our timing check, ~20-25% faster.
    dev = device()
    kw = dict(device=dev, max_length=512)
    if dev in ("mps", "cuda"):
        kw["model_kwargs"] = {"torch_dtype": torch.float16}
    try:
        return CrossEncoder(name, local_files_only=True, **kw)
    except OSError:
        return CrossEncoder(name, **kw)


def rerank(conn: psycopg.Connection, question: str, hits: list[r.Hit], top: int,
           reranker: str = "base") -> list[r.Hit]:
    """Rescore the first `top` hits with a cross-encoder; the rest keep their order after them."""
    head, tail = hits[:top], hits[top:]
    if not head:
        return hits
    # Same contextual header the embeddings get, plus the date: "Revenue grew 23%" only answers
    # "latest quarter at Walmart" if the scorer can see whose filing it is and when it came out.
    texts = {cid: f"{name} | {form} | {title} | published {pub:%Y-%m-%d}\n{text}"
             for cid, name, form, title, pub, text in conn.execute(
                 """SELECT c.id, co.name, d.form, coalesce(s.title, c.section), d.published_at, c.text
                    FROM chunks c JOIN documents d ON d.id = c.document_id
                    JOIN companies co ON co.id = c.company_id JOIN sections s ON s.id = c.section_id
                    WHERE c.id = ANY(%s)""", ([h.chunk_id for h in head],))}
    scores = _cross_encoder(RERANKERS[reranker]).predict(
        [(question, texts[h.chunk_id]) for h in head], batch_size=16, show_progress_bar=False)
    order = sorted(zip(head, scores, strict=True), key=lambda x: -float(x[1]))
    out = [r.Hit(h.chunk_id, i + 1, float(s)) for i, (h, s) in enumerate(order)]
    return out + [r.Hit(h.chunk_id, len(out) + i + 1, h.score) for i, h in enumerate(tail)]


def search(
    conn: psycopg.Connection,
    question: str,
    cfg: SearchConfig,
    *,
    tickers: list[str] | None = None,
    as_of: datetime | None = None,
) -> list[r.Hit]:
    company_ids = r.company_ids_for(conn, tickers) if tickers else None
    latest = cfg.recency == "always" or (cfg.recency == "auto" and r.wants_latest(question))
    kw = dict(chunker=cfg.chunker, company_ids=company_ids, as_of=as_of, latest_only=latest)
    pool = max(cfg.candidates, cfg.rerank_top if cfg.rerank else 0, cfg.k)
    if cfg.mode == "vector":
        hits = r.vector_search(conn, question, model=cfg.model, k=pool, **kw)
    elif cfg.mode == "keyword":
        hits = r.keyword_search(conn, question, k=pool, **kw)
    else:
        hits = r.rrf(r.vector_search(conn, question, model=cfg.model, k=cfg.candidates, **kw),
                     r.keyword_search(conn, question, k=cfg.candidates, **kw), limit=pool)
    if cfg.rerank:
        hits = rerank(conn, question, hits, cfg.rerank_top, cfg.reranker)
    return hits[: cfg.k]
