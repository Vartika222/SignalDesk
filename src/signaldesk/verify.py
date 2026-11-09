"""Quote verifier. Hand-written on purpose: this is the part that must be trusted, so it is not a model.

A claim passes if its quote appears in the passage it cites:
- exact:      byte-for-byte substring
- normalized: equal after collapsing whitespace and unifying curly quotes and dashes (case kept)
Anything else fails, including a real quote attributed to the wrong passage.

This proves the quote exists, not that it supports the claim. Faithfulness is graded separately.
"""

from __future__ import annotations

from dataclasses import dataclass

from signaldesk.evaluation import normalize

MIN_QUOTE_CHARS = 12  # "Revenue" alone proves nothing


@dataclass
class ClaimCheck:
    text: str
    passage_id: str
    quote: str
    result: str  # exact | normalized | fail_missing_passage | fail_too_short | fail_not_found

    @property
    def ok(self) -> bool:
        return self.result in ("exact", "normalized")


def check_quote(quote: str, passage: str | None) -> str:
    if passage is None:
        return "fail_missing_passage"
    if len(normalize(quote)) < MIN_QUOTE_CHARS:
        return "fail_too_short"
    if quote in passage:
        return "exact"
    if normalize(quote) in normalize(passage):
        return "normalized"
    return "fail_not_found"


def verify_claims(claims: list[dict], passages: dict[str, str]) -> list[ClaimCheck]:
    return [
        ClaimCheck(c.get("text", ""), c.get("passage_id", ""), c.get("quote", ""),
                   check_quote(c.get("quote", ""), passages.get(c.get("passage_id", ""))))
        for c in claims
    ]
