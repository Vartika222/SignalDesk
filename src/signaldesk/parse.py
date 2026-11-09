"""EDGAR HTML → clean text → sections.

Design choices (tested in notebook 01):
- Tables are kept as pipe-delimited rows, because filings put guidance and figures in tables.
- Inline-XBRL hidden headers are removed.
- 10-K/10-Q are split on "Item N." headings. The table of contents is detected first (heading lines
  ending in page numbers) and skipped. After it, the first occurrence of each item wins, in
  ascending order, which ignores running page headers and backward cross-references.
- Text the rules can't label is kept as `unlabelled` rather than dropped (v1 lost it silently).
- 8-K press releases (EX-99.1) are split into body / outlook / safe-harbor.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass

from bs4 import BeautifulSoup, NavigableString, Tag, XMLParsedAsHTMLWarning

# Inline-XBRL filings are XHTML; the HTML parser handles them fine and keeps tables intact.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

PARSER_VERSION = "html-v3"

BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "blockquote", "pre", "hr", "center", "font-block",
}


@dataclass(frozen=True)
class Section:
    section: str  # canonical key, e.g. risk_factors, mdna, press_release
    title: str
    text: str


# ---------------------------------------------------------------------------------------------
# HTML → text


def _cell_text(cell: Tag) -> str:
    return re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()


def _table_to_text(table: Tag) -> str:
    """Render a table as 'a | b | c' rows, merging the $ ( ) % fragments SEC tables split apart."""
    lines = []
    for tr in table.find_all("tr"):
        cells = [_cell_text(td) for td in tr.find_all(["td", "th"])]
        merged: list[str] = []
        for c in cells:
            if not c:
                continue
            if merged and c in {")", "%", ")%", "%)"} or merged and merged[-1] in {"$", "(", "($", "$("}:
                merged[-1] += c
            else:
                merged.append(c)
        if merged:
            lines.append(" | ".join(merged))
    return "\n".join(lines)


def html_to_text(raw: bytes | str) -> str:
    soup = BeautifulSoup(raw, "lxml")
    for t in soup(["script", "style", "head", "title", "noscript"]):
        t.decompose()
    for t in soup.find_all(re.compile(r"^ix:header$", re.I)):
        t.decompose()
    for t in soup.find_all(style=re.compile(r"display\s*:\s*none", re.I)):
        t.decompose()

    # Tables first: replace each outermost table with its pipe rendering.
    for table in soup.find_all("table"):
        if table.find_parent("table") is not None:
            continue
        rendered = _table_to_text(table)
        table.replace_with(NavigableString(f"\n\n{rendered}\n\n" if rendered else "\n"))

    for tag in soup.find_all(BLOCK_TAGS):
        if tag.name == "br":
            tag.replace_with(NavigableString("\n"))
            continue
        tag.insert_before(NavigableString("\n\n"))
        tag.insert_after(NavigableString("\n\n"))

    text = soup.get_text("")
    return normalize_text(text)


_ZW = dict.fromkeys(map(ord, "​‌‍﻿"), None)


def normalize_text(text: str) -> str:
    text = text.replace("\xa0", " ").replace(" ", " ").translate(_ZW)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t\f\v]+", " ", ln).strip() for ln in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------------------------
# Sectioning

TENK_ITEMS = {
    "1": ("business", "Business"),
    "1a": ("risk_factors", "Risk Factors"),
    "1b": ("unresolved_staff_comments", "Unresolved Staff Comments"),
    "1c": ("cybersecurity", "Cybersecurity"),
    "2": ("properties", "Properties"),
    "3": ("legal_proceedings", "Legal Proceedings"),
    "4": ("mine_safety", "Mine Safety Disclosures"),
    "5": ("market_for_equity", "Market for Registrant's Common Equity"),
    "6": ("reserved", "[Reserved]"),
    "7": ("mdna", "Management's Discussion and Analysis"),
    "7a": ("market_risk", "Quantitative and Qualitative Disclosures About Market Risk"),
    "8": ("financial_statements", "Financial Statements and Supplementary Data"),
    "9": ("accountant_changes", "Changes in and Disagreements with Accountants"),
    "9a": ("controls", "Controls and Procedures"),
    "9b": ("other_information", "Other Information"),
    "9c": ("foreign_jurisdiction", "Disclosure Regarding Foreign Jurisdictions"),
    "10": ("directors", "Directors, Executive Officers and Corporate Governance"),
    "11": ("executive_compensation", "Executive Compensation"),
    "12": ("security_ownership", "Security Ownership"),
    "13": ("relationships", "Certain Relationships and Related Transactions"),
    "14": ("accountant_fees", "Principal Accountant Fees and Services"),
    "15": ("exhibits", "Exhibits and Financial Statement Schedules"),
    "16": ("form_summary", "Form 10-K Summary"),
}

TENQ_ITEMS = {
    ("1", "1"): ("financial_statements", "Financial Statements"),
    ("1", "2"): ("mdna", "Management's Discussion and Analysis"),
    ("1", "3"): ("market_risk", "Quantitative and Qualitative Disclosures About Market Risk"),
    ("1", "4"): ("controls", "Controls and Procedures"),
    ("2", "1"): ("legal_proceedings", "Legal Proceedings"),
    ("2", "1a"): ("risk_factors", "Risk Factors"),
    ("2", "2"): ("equity_repurchases", "Unregistered Sales of Equity Securities"),
    ("2", "3"): ("senior_defaults", "Defaults Upon Senior Securities"),
    ("2", "4"): ("mine_safety", "Mine Safety Disclosures"),
    ("2", "5"): ("other_information", "Other Information"),
    ("2", "6"): ("exhibits", "Exhibits"),
}

# "Item 1A." / "ITEM 7 —" / "Item 2. | Management's..." at the start of a line.
RE_ITEM = re.compile(r"(?im)^[ \t]*item[ \t]+(\d{1,2}[a-c]?)(?=[\s.:\-–—|]|$)")
RE_PART = re.compile(r"(?im)^[ \t]*part[ \t]+(iv|i{1,3})(?=[\s.:\-–—|]|$)")
ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4"}


# A TOC entry ends in a page reference: "| 33", "| 9-31", "| Page", or dot leaders "..... 33".
RE_TOC_LINE = re.compile(r"(?i)(\|\s*(page|\d{1,3}(\s*[-–]\s*\d{1,3})?)|\.{3,}\s*\d{1,3})\s*$")


RE_BARE_HEAD = re.compile(r"(?i)^\s*(item\s+\d{1,2}\s*[a-c]?|part\s+[ivx]+)\s*\.?\s*$")


def _line_at(text: str, pos: int) -> str:
    end = text.find("\n", pos)
    return text[pos : end if end >= 0 else len(text)]


def _toc_end(text: str) -> int:
    """End offset of the table of contents (0 if none found). Looks only at the first fifth.

    Primary signal: heading lines that end in a page number. Fallback: the first dense run of five or
    more Item headings within 300 characters of each other (TOCs without page numbers still pack
    headings closely together)."""
    limit = max(len(text) // 5, 20_000)
    heads = sorted([m.start() for m in RE_ITEM.finditer(text, 0, limit)]
                   + [m.start() for m in RE_PART.finditer(text, 0, limit)])
    end = 0
    for pos in heads:
        line = _line_at(text, pos)
        if RE_TOC_LINE.search(line):
            end = pos + len(line)
    if end:
        # Some TOCs put a bare "Item 1." on its own line with the title and page on later lines.
        # Extend through Part headings and bare Item headings that follow closely; a titled Item
        # heading means the body began.
        for pos in heads:
            if pos < end:
                continue
            line = _line_at(text, pos)
            is_part = RE_PART.match(line) is not None
            if pos - end < 500 and (is_part or RE_BARE_HEAD.match(line) or RE_TOC_LINE.search(line)):
                end = pos + len(line)
            else:
                break
        return end
    items = [m.start() for m in RE_ITEM.finditer(text, 0, limit)]
    run = 1
    for a, b in zip(items, items[1:], strict=False):
        if b - a < 300:
            run += 1
            if run >= 5:
                end = b + len(_line_at(text, b))
        elif end:
            break
        else:
            run = 1
    return end


def _split(text: str, starts: list[tuple[int, str, str]], toc_end: int = 0) -> list[Section]:
    """Cut text at the chosen heading positions.

    [0, toc_end) is front matter (not indexed). Text between the TOC and the first recognised heading
    is kept as 'unlabelled' so it stays searchable. JPM-style filings put MD&A there."""
    starts = sorted(starts)
    out: list[Section] = []
    first = starts[0][0] if starts else len(text)
    if toc_end and text[:toc_end].strip():
        out.append(Section("front_matter", "Cover and contents", text[:toc_end].strip()))
        gap = text[toc_end:first].strip()
        if len(gap) > 500:
            out.append(Section("unlabelled", "Unlabelled", gap))
    elif starts and first > 0 and text[:first].strip():
        out.append(Section("front_matter", "Cover and contents", text[:first].strip()))
    for i, (pos, key, title) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        body = text[pos:end].strip()
        if body:
            out.append(Section(key, title, body))
    if not out and text:
        out.append(Section("body", "Body", text))
    return out


TENK_ORDER = list(TENK_ITEMS)
TENQ_ORDER = list(TENQ_ITEMS)


def sectionize_10k(text: str) -> list[Section]:
    """First occurrence of each Item after the TOC, in ascending item order. Running page headers
    ("Item 7. MD&A - Liquidity") and backward cross-references repeat earlier items, so they are skipped."""
    toc = _toc_end(text)
    chosen: dict[str, int] = {}
    last = -1
    for m in RE_ITEM.finditer(text, toc):
        k = m.group(1).lower()
        if k in TENK_ITEMS and k not in chosen and TENK_ORDER.index(k) > last:
            chosen[k] = m.start()
            last = TENK_ORDER.index(k)
    return _split(text, [(p, *TENK_ITEMS[k]) for k, p in chosen.items()], toc)


def sectionize_10q(text: str) -> list[Section]:
    """Like 10-K, but item numbers restart in Part II. The part comes from a PART II heading in the body
    or from the item numbers restarting, never from the TOC."""
    toc = _toc_end(text)
    events = sorted([(m.start(), "part", ROMAN[m.group(1).lower()]) for m in RE_PART.finditer(text, toc)]
                    + [(m.start(), "item", m.group(1).lower()) for m in RE_ITEM.finditer(text, toc)])
    part, last = "1", -1
    chosen: dict[tuple[str, str], int] = {}
    for pos, kind, val in events:
        if kind == "part":
            if val > part:
                part = val
            continue
        if part == "1" and last >= 0 and (val in {"1", "1a"}) and TENQ_ORDER[last][0] == "1" \
                and TENQ_ORDER[last][1] in {"3", "4"}:
            part = "2"  # numbering restarted without an explicit PART II heading
        key = (part, val)
        if key in TENQ_ITEMS and key not in chosen and TENQ_ORDER.index(key) > last:
            chosen[key] = pos
            last = TENQ_ORDER.index(key)
    return _split(text, [(p, *TENQ_ITEMS[k]) for k, p in chosen.items()], toc)


RE_OUTLOOK = re.compile(
    r"(?im)^[ \t]*((?:business |financial |fiscal \S+ |full[- ]year |\S+ quarter )?"
    r"(?:outlook|guidance)(?: for .{0,40})?)[ \t]*:?[ \t]*$"
)
RE_SAFE_HARBOR = re.compile(
    r"(?im)^[ \t]*((?:cautionary |special )?(?:note |statement[s]? )?(?:regarding |about |on )?"
    r"forward[- ]looking statements?.{0,40})[ \t]*$"
)


def sectionize_press_release(text: str) -> list[Section]:
    starts: list[tuple[int, str, str]] = [(0, "press_release", "Press release")]
    m = RE_OUTLOOK.search(text)
    if m:
        starts.append((m.start(), "outlook", m.group(1).strip()))
    s = RE_SAFE_HARBOR.search(text)
    if s and (not m or s.start() > m.start()):
        starts.append((s.start(), "safe_harbor", "Forward-looking statements"))
    return _split(text, starts)


def sectionize(text: str, form: str) -> list[Section]:
    if form == "10-K":
        secs = sectionize_10k(text)
    elif form == "10-Q":
        secs = sectionize_10q(text)
    elif form == "8-K":
        secs = sectionize_press_release(text)
    else:
        secs = []
    return secs or ([Section("body", "Body", text)] if text else [])


def parse_document(raw: bytes, form: str) -> list[Section]:
    return sectionize(html_to_text(raw), form)
