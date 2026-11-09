"""Labelled questions, relevance judged by gold quote spans, retrieval metrics with bootstrap CIs.

A gold label is (accession_no, quote). A retrieved chunk is relevant to a gold quote if it comes from
that filing and covers at least half of the quote's characters. Judging by quote span rather than
chunk id keeps the truth fixed while the chunker changes.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg

# ---------------------------------------------------------------------------------------------
# Text normalisation and span finding

_TRANS = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", "\xa0": " ",
})


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.translate(_TRANS)).strip()


def _normalized_with_map(s: str) -> tuple[str, list[int]]:
    """Normalise like `normalize` (but keep case) and return, for each output char, its source index."""
    out, idx, prev_space = [], [], True
    for i, ch in enumerate(s.translate(_TRANS)):
        if ch.isspace():
            if not prev_space:
                out.append(" ")
                idx.append(i)
            prev_space = True
        else:
            out.append(ch)
            idx.append(i)
            prev_space = False
    if out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def find_span(haystack: str, needle: str) -> tuple[int, int] | None:
    """Locate needle in haystack, tolerant of whitespace, quote and dash differences and case.
    Returns (start, end) offsets into the *original* haystack."""
    norm, idx = _normalized_with_map(haystack)
    n = normalize(needle)
    if not n:
        return None
    pos = norm.find(n)
    if pos < 0:
        pos = norm.lower().find(n.lower())
    if pos < 0:
        return None
    return idx[pos], idx[pos + len(n) - 1] + 1


# ---------------------------------------------------------------------------------------------
# Questions


@dataclass
class Gold:
    accession_no: str
    quote: str


@dataclass
class Question:
    id: str
    split: str
    text: str
    labelled_by: str
    gold: list[Gold]
    ticker: str | None = None
    as_of: datetime | None = None
    gold_answer: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def answerable(self) -> bool:
        return bool(self.gold)


def load_questions(path: str | Path) -> list[Question]:
    out = []
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        d = json.loads(line)
        if not d.get("gold") and d.get("answerable", True):
            raise ValueError(f"{path}:{n}: empty gold requires \"answerable\": false")
        try:
            out.append(Question(
                id=d["id"], split=d["split"], text=d["text"], labelled_by=d["labelled_by"],
                gold=[Gold(g["accession_no"], g["quote"]) for g in d["gold"]],
                ticker=d.get("ticker"),
                as_of=datetime.fromisoformat(d["as_of"]) if d.get("as_of") else None,
                gold_answer=d.get("gold_answer"),
            ))
        except KeyError as e:
            raise ValueError(f"{path}:{n}: missing field {e}") from None
    return out


@dataclass
class GoldSpan:
    gold_index: int
    document_id: int
    section_id: int
    start: int
    end: int


def resolve_gold(conn: psycopg.Connection, q: Question) -> tuple[list[GoldSpan], list[str]]:
    """Find every gold quote in the parsed sections of its filing. Returns spans and errors."""
    spans, errors = [], []
    for gi, g in enumerate(q.gold):
        rows = conn.execute(
            """SELECT s.id, s.document_id, s.text FROM sections s
               JOIN documents d ON d.id = s.document_id
               WHERE d.accession_no = %s ORDER BY s.id""",
            (g.accession_no,),
        ).fetchall()
        if not rows:
            errors.append(f"{q.id}: gold[{gi}] accession {g.accession_no} not in database")
            continue
        found = False
        for sid, did, text in rows:
            sp = find_span(text, g.quote)
            if sp:
                spans.append(GoldSpan(gi, did, sid, *sp))
                found = True
                break
        if not found:
            errors.append(f"{q.id}: gold[{gi}] quote not found verbatim in {g.accession_no}: "
                          f"{g.quote[:70]!r}")
    return spans, errors


# ---------------------------------------------------------------------------------------------
# Metrics


def relevant_gold(chunk: tuple[int, int, int], spans: list[GoldSpan], min_cover: float = 0.5) -> set[int]:
    """chunk = (section_id, char_start, char_end). Returns indices of gold quotes it covers."""
    sid, cs, ce = chunk
    hit = set()
    for g in spans:
        if g.section_id != sid:
            continue
        overlap = min(ce, g.end) - max(cs, g.start)
        if overlap > 0 and overlap >= min_cover * (g.end - g.start):
            hit.add(g.gold_index)
    return hit


def score_ranking(ranked_gold: list[set[int]], n_gold: int, ks=(1, 3, 5, 10)) -> dict[str, float]:
    """ranked_gold[i] = gold indices covered by the chunk at rank i+1."""
    m: dict[str, float] = {}
    for k in ks:
        covered = set().union(*ranked_gold[:k]) if ranked_gold[:k] else set()
        m[f"recall@{k}"] = len(covered) / n_gold if n_gold else math.nan
        m[f"hit@{k}"] = float(bool(covered))
    first = next((i for i, g in enumerate(ranked_gold) if g), None)
    m["mrr"] = 1.0 / (first + 1) if first is not None else 0.0
    dcg = sum(1.0 / math.log2(i + 2) for i, g in enumerate(ranked_gold[:10]) if g)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(n_gold, 10)))
    m["ndcg@10"] = dcg / idcg if idcg else math.nan
    return m


def bootstrap_ci(values, n_boot: int = 2000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float, float]:
    v = np.asarray([x for x in values if not (isinstance(x, float) and math.isnan(x))], dtype=float)
    if len(v) == 0:
        return (math.nan, math.nan, math.nan)
    rng = np.random.default_rng(seed)
    means = rng.choice(v, size=(n_boot, len(v)), replace=True).mean(axis=1)
    return float(v.mean()), float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


Retriever = Callable[[Question], list[int]]  # returns ranked chunk ids


def lenient_gold(chunk_text: str, quotes_norm: list[str]) -> set[int]:
    """Gold indices whose full quote appears in this chunk, from whichever filing it came from. A
    company repeats sentences across filings; this counts those, the strict metric does not."""
    t = normalize(chunk_text).lower()
    return {i for i, q in enumerate(quotes_norm) if q and q in t}


def evaluate(conn: psycopg.Connection, questions: list[Question], retriever: Retriever,
             k: int = 10) -> pd.DataFrame:
    """Score retrieval per question. Can't-answer questions (no gold) are skipped here; Phase 2's
    answer evaluation uses them to test refusals."""
    rows = []
    for q in questions:
        if not q.answerable:
            continue
        spans, errors = resolve_gold(conn, q)
        if errors:
            raise ValueError("unresolvable gold labels:\n" + "\n".join(errors))
        ids = retriever(q)[:k]
        info = {}
        if ids:
            info = {r[0]: (r[1], r[2], r[3], r[4]) for r in conn.execute(
                "SELECT id, section_id, char_start, char_end, text FROM chunks WHERE id = ANY(%s)", (ids,))}
        quotes = [normalize(g.quote).lower() for g in q.gold]
        strict = [relevant_gold(info[c][:3], spans) if c in info else set() for c in ids]
        lenient = [(strict[i] | lenient_gold(info[c][3], quotes)) if c in info else set()
                   for i, c in enumerate(ids)]
        m = score_ranking(strict, len(q.gold))
        ml = score_ranking(lenient, len(q.gold))
        rows.append({"question_id": q.id, "split": q.split, "n_gold": len(q.gold), "retrieved": ids,
                     **m, **{f"{key}_lenient": v for key, v in ml.items()}})
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame, metrics=("recall@5", "mrr", "ndcg@10")) -> dict[str, str]:
    out = {"n": str(len(df))}
    for m in metrics:
        mean, lo, hi = bootstrap_ci(df[m].tolist())
        out[m] = f"{mean:.3f} [{lo:.3f}, {hi:.3f}]"
    return out
