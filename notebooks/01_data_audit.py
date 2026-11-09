# ---
# jupyter:
#   jupytext:
#     formats: py:percent
#   kernelspec:
#     display_name: Python 3
#     name: python3
# ---

# %% [markdown]
# # 01 · Data audit
#
# **Question:** What did we fetch, what is missing, and are publication times right?
#
# Every later number depends on `published_at`. If it is wrong, every backtest leaks the future.
# This notebook checks it three ways before anything else is built on it.

# %%
import warnings

import pandas as pd

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

from signaldesk.config import REPO_ROOT, settings
from signaldesk.db import connect

REPORTS = REPO_ROOT / "reports"
REPORTS.mkdir(exist_ok=True)
pd.set_option("display.width", 160, "display.max_colwidth", 80)

with connect() as conn:
    docs = pd.read_sql(
        """SELECT d.id, co.ticker, d.form, d.doc_kind, d.accession_no, d.filed_date,
                  d.period_of_report, d.accepted_at, d.accepted_at_json, d.published_at,
                  d.raw_path, d.raw_bytes, d.content_hash
           FROM documents d JOIN companies co ON co.id = d.company_id
           ORDER BY co.ticker, d.published_at""",
        conn,
    )
    sections = pd.read_sql(
        """SELECT s.document_id, s.section, length(s.text) AS chars
           FROM sections s""",
        conn,
    )
    chunks = pd.read_sql(
        "SELECT chunker, section, token_count FROM chunks", conn
    )
print(f"{len(docs)} documents, {docs.ticker.nunique()} companies")

# %% [markdown]
# ## 1. Coverage: documents per company and form

# %%
coverage = docs.pivot_table(index="ticker", columns="form", values="id", aggfunc="count", fill_value=0)
coverage["total"] = coverage.sum(axis=1)
coverage.to_csv(REPORTS / "01_coverage.csv")
coverage

# %% [markdown]
# ## 2. Publication-time checks
#
# `accepted_at` comes from the filing's index page ("Accepted", US Eastern) and is the point-in-time
# key. The submissions JSON also carries `acceptanceDateTime`. We check:
#
# - **a.** accepted time is never after the EDGAR filing date's end (filings accepted after
#   17:30 ET are dated the next business day, so `filed_date` may be one business day later);
# - **b.** whether the JSON timestamp agrees with the index page, and if not, by how much;
# - **c.** what share of filings arrive outside market hours (that decides the tradable bar).

# %%
et = docs.accepted_at.dt.tz_convert("America/New_York")
docs["accepted_et"] = et
docs["accepted_date_et"] = et.dt.date
docs["lag_days"] = (pd.to_datetime(docs.filed_date) - pd.to_datetime(docs.accepted_date_et)).dt.days

check_a = docs[(docs.lag_days < 0) | (docs.lag_days > 4)]
print(f"a. filed_date before acceptance date or > 4 days after: {len(check_a)} of {len(docs)}")
check_a[["ticker", "form", "accession_no", "accepted_et", "filed_date"]]

# %%
both = docs.dropna(subset=["accepted_at_json"]).copy()
both["json_minus_index_h"] = (both.accepted_at_json - both.accepted_at).dt.total_seconds() / 3600
print("b. JSON acceptanceDateTime minus index-page Accepted (hours):")
print(both.json_minus_index_h.round(2).value_counts().sort_index().to_string())

# %% [markdown]
# **What we expected vs what we found.** The hypothesis was that the JSON's trailing "Z" might be
# misleading (an Eastern clock labelled UTC), which would show up as a +4/+5 hour gap. On the dev slice
# the two sources agree to the second for every filing, so the JSON timestamp really is UTC. The
# ingester still treats the index page as the source of truth, and this cell stays as a regression
# check for when the universe grows.

# %%
assert (both.json_minus_index_h.abs() < 1 / 60).all(), "JSON and index-page acceptance times disagree"

# %%
t = docs.accepted_et.dt.hour + docs.accepted_et.dt.minute / 60
docs["session"] = pd.cut(t, [-0.1, 9.5, 16.0, 24.1], labels=["pre-market", "market hours", "after close"])
session = docs.groupby(["form", "session"], observed=False).size().unstack(fill_value=0)
session.to_csv(REPORTS / "01_sessions.csv")
print("c. When filings are accepted (US Eastern):")
session

# %% [markdown]
# **Tradable rule (PRD v1.1):** an event can be acted on only at the first market open strictly after
# `published_at`. A pre-market release trades at that day's open. An after-close or market-hours
# release trades at the next session's open. No same-bar trading.

# %% [markdown]
# ## 3. Integrity: every document traces to a raw file

# %%
import hashlib

missing, mismatched = [], []
for r in docs.itertuples():
    p = settings.data_dir / r.raw_path
    if not p.exists():
        missing.append(r.raw_path)
    elif hashlib.sha256(p.read_bytes()).hexdigest() != r.content_hash:
        mismatched.append(r.raw_path)
print(f"raw files missing: {len(missing)}; hash mismatches: {len(mismatched)}")
assert not missing and not mismatched

# %% [markdown]
# ## 4. Parse quality: did sectioning find the parts we need?
#
# Guidance lives in MD&A and press-release outlooks, and risk language lives in Risk Factors. A
# document where those weren't found is a parser failure to inspect, not to average away.

# %%
need = {"10-K": ["risk_factors", "mdna"], "10-Q": ["mdna"], "8-K": ["press_release"]}
found = sections.groupby("document_id").section.apply(set)
rows = []
for r in docs.itertuples():
    have = found.get(r.id, set())
    for s in need.get(r.form, []):
        rows.append({"ticker": r.ticker, "form": r.form, "accession_no": r.accession_no,
                     "section": s, "found": s in have})
parse_q = pd.DataFrame(rows)
summary = parse_q.groupby(["form", "section"]).found.agg(["mean", "count"])
summary.columns = ["share_found", "n"]
summary.to_csv(REPORTS / "01_section_coverage.csv")
summary

# %%
print("Documents missing a required section (inspect these by hand):")
parse_q[~parse_q.found]

# %% [markdown]
# ### "Found" is not "correct": size checks
#
# Parser v1 passed the check above on every document and was still wrong. It took DAL's
# table-of-contents line as its MD&A (98 characters) and filed the real MD&A under a Part II item.
# So we also flag sections whose size is implausible. A tiny MD&A or financial-statements section is
# usually a TOC line or a cross-reference. A huge catch-all section (controls, exhibits) usually means
# the next heading was missed.
#
# Short 10-Q risk-factor sections are often genuine ("no material changes since the 10-K"). Those are
# listed separately because they matter for Phase 3: new risk language has to be measured against
# the 10-K.

# %%
sec_sizes = sections.merge(docs[["id", "ticker", "form", "accession_no"]], left_on="document_id", right_on="id")
too_small = sec_sizes.section.isin(["mdna", "financial_statements"]) & (sec_sizes.chars < 3000)
too_big = ~sec_sizes.section.isin(["financial_statements", "business", "risk_factors", "mdna", "unlabelled"]) \
    & (sec_sizes.chars > 150_000)
suspicious = sec_sizes[too_small | too_big][["ticker", "form", "accession_no", "section", "chars"]]
suspicious.to_csv(REPORTS / "01_suspicious_sections.csv", index=False)
print(f"{len(suspicious)} suspicious sections in {suspicious.accession_no.nunique()} documents")
suspicious

# %%
short_rf = sec_sizes[(sec_sizes.section == "risk_factors") & (sec_sizes.chars < 3000)]
print(f"10-Q risk-factor sections under 3,000 chars (usually 'no material changes'): {len(short_rf)}")
short_rf[["ticker", "form", "chars"]]

# %%
unl = sec_sizes[sec_sizes.section == "unlabelled"]
print(f"'unlabelled' sections kept for search: {len(unl)}, {unl.chars.sum():,} chars")
unl[["ticker", "form", "chars"]]

# %% [markdown]
# **Known limitation (parser html-v2).** JPMorgan files a *cross-reference* 10-K: Items 7 and 8 just
# point to pages of an embedded annual report, so the real MD&A and financial statements land inside
# `exhibits`. Its 10-Qs have no Item 1/2 headings at all, so that text is kept as `unlabelled`.
# Nothing is lost, and retrieval still finds it, but section-level filters (for example "MD&A only")
# miss it. A title-based or layout-aware parser is the fix. That is the parser comparison the PRD lists,
# to be measured once the study universe shows how common this layout is.

# %%
sec_sizes.groupby(["form", "section"]).chars.describe()[["count", "50%", "min", "max"]].astype(int)

# %% [markdown]
# ## 5. Chunk sizes per chunker

# %%
chunks.groupby("chunker").token_count.describe()[["count", "mean", "50%", "max"]].round(1)

# %% [markdown]
# ## 6. Hand-check sample
#
# Five random sections, printed raw. Read them against the filing on sec.gov: is the text clean,
# are tables legible, does the section start where it should?

# %%
with connect() as conn:
    sample = conn.execute(
        """SELECT co.ticker, d.form, s.section, d.source_url, left(s.text, 700)
           FROM sections s JOIN documents d ON d.id = s.document_id
           JOIN companies co ON co.id = d.company_id
           WHERE s.section <> 'front_matter'
           ORDER BY md5(s.id::text) LIMIT 5"""
    ).fetchall()
for t, form, sec, url, text in sample:
    print(f"── {t} {form} [{sec}]  {url}\n{text}\n")
