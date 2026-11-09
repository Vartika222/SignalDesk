"""Section text → passages.

Every chunk is an exact substring of its section (text == section_text[char_start:char_end]), so any
cited passage can be traced back to the raw filing.

Configs compared in notebook 02:
- sec256 / sec512: paragraph-aware packing (paragraph → line → sentence → token fallback) with
  overlap, sized for a 512-token embedding window that also holds a short contextual header.
- fixed512: blind token windows of the same size, ignoring paragraph and sentence boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

TOKENIZER_NAME = "BAAI/bge-small-en-v1.5"  # bge-small and bge-base share this vocabulary


@dataclass(frozen=True)
class ChunkerConfig:
    name: str
    max_tokens: int  # body budget; the header and special tokens fit in the rest of 512
    overlap_tokens: int
    boundary_aware: bool = True


CHUNKERS: dict[str, ChunkerConfig] = {
    "sec256": ChunkerConfig("sec256", max_tokens=240, overlap_tokens=32),
    "sec512": ChunkerConfig("sec512", max_tokens=470, overlap_tokens=64),
    "fixed512": ChunkerConfig("fixed512", max_tokens=470, overlap_tokens=64, boundary_aware=False),
}


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    char_start: int
    char_end: int
    token_count: int
    text: str


@lru_cache(maxsize=1)
def tokenizer():
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(TOKENIZER_NAME)


def count_tokens(texts: list[str]) -> list[int]:
    if not texts:
        return []
    enc = tokenizer()(texts, add_special_tokens=False)["input_ids"]
    return [len(x) for x in enc]


# A unit is a (start, end) span in the section text that we never split further at this level.
Span = tuple[int, int]

RE_PARA = re.compile(r"\n\s*\n")
RE_SENT = re.compile(r"(?<=[.!?;])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9$•])")


def _split_spans(text: str, start: int, end: int, pattern: re.Pattern) -> list[Span]:
    out, cur = [], start
    for m in pattern.finditer(text, start, end):
        if m.start() > cur:
            out.append((cur, m.start()))
        cur = m.end()
    if cur < end:
        out.append((cur, end))
    return [(s, e) for s, e in out if text[s:e].strip()]


def _token_windows(text: str, start: int, end: int, size: int, overlap: int) -> list[Span]:
    """Hard split by tokens, mapped back to character offsets."""
    piece = text[start:end]
    enc = tokenizer()(piece, add_special_tokens=False, return_offsets_mapping=True)
    offs = enc["offset_mapping"]
    if not offs:
        return []
    out, i, step = [], 0, max(1, size - overlap)
    while i < len(offs):
        j = min(i + size, len(offs))
        s, e = offs[i][0], offs[j - 1][1]
        out.append((start + s, start + e))
        if j == len(offs):
            break
        i += step
    return out


def _units(text: str, span: Span, limit: int) -> list[tuple[Span, int]]:
    """Break a span into pieces of at most `limit` tokens, preferring natural boundaries."""
    levels = [RE_PARA, re.compile(r"\n"), RE_SENT]
    out: list[tuple[Span, int]] = []

    def rec(s: int, e: int, depth: int):
        n = count_tokens([text[s:e]])[0]
        if n <= limit:
            out.append(((s, e), n))
            return
        if depth >= len(levels):
            for ws, we in _token_windows(text, s, e, limit, 0):
                out.append(((ws, we), count_tokens([text[ws:we]])[0]))
            return
        parts = _split_spans(text, s, e, levels[depth])
        if len(parts) <= 1:
            rec(s, e, depth + 1)
            return
        for ps, pe in parts:
            rec(ps, pe, depth + 1)

    for ps, pe in _split_spans(text, span[0], span[1], RE_PARA):
        rec(ps, pe, 1)
    return out


def chunk_text(text: str, cfg: ChunkerConfig) -> list[Chunk]:
    if not text.strip():
        return []
    if not cfg.boundary_aware:
        spans = _token_windows(text, 0, len(text), cfg.max_tokens, cfg.overlap_tokens)
        counts = count_tokens([text[s:e] for s, e in spans])
        return [Chunk(i, s, e, n, text[s:e]) for i, ((s, e), n) in enumerate(zip(spans, counts, strict=True))]

    units = _units(text, (0, len(text)), cfg.max_tokens)
    chunks: list[Chunk] = []
    i = 0
    while i < len(units):
        j, total = i, 0
        while j < len(units) and (total + units[j][1] <= cfg.max_tokens or j == i):
            total += units[j][1]
            j += 1
        s, e = units[i][0][0], units[j - 1][0][1]
        chunks.append(Chunk(len(chunks), s, e, count_tokens([text[s:e]])[0], text[s:e]))
        if j >= len(units):
            break
        # Overlap: step back over trailing units that fit in the overlap budget.
        back, k = 0, j
        while k - 1 > i and back + units[k - 1][1] <= cfg.overlap_tokens:
            back += units[k - 1][1]
            k -= 1
        i = k
    return chunks
