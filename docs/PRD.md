# Signal Desk — PRD v1.1

Revised 6 Nov 2026 · owner: Vartika

Signal Desk turns SEC filings into cited, searchable signals, lets agents propose trading rules on
those signals, and lets a skeptic try to break them. The headline result is one sentence:

> From **N** filings I extracted **M** signals and proposed **K** rules; **J** survived correction for
> multiple testing.

J is expected to be small. Reporting that honestly is the point.

Research only: no live trading, no order execution, no investment advice.

---

## What changed from v1.0 (and why)

| # | Problem in v1.0 | Fix in v1.1 |
|---|---|---|
| 1 | ~50 filings cannot yield ≥30 events per signal type, so the event study was underpowered by design | Two data scales: a **dev slice** (~50 docs, 10 companies) for Phases 1–2, and a **study universe** (every 8-K Item 2.02 earnings release from a few hundred companies over several years — thousands of events) for Phase 3 onward |
| 2 | Market undecided | **US / SEC EDGAR**: free official API, published fair-access policy, and an acceptance timestamp on every filing |
| 3 | Go layer had no job beyond "I want Go" | Go owns **ingestion** (rate-limited, retrying, long-running, network-bound — must never block analysis) and later the **API**. Python owns parsing, ML and statistics. The queue is a Postgres table (`FOR UPDATE SKIP LOCKED`), not Redis/NATS — one less moving part at this scale |
| 4 | Skeptic was LLM-only, so a "100% catch rate" could be luck | Skeptic = **deterministic checks first** (timestamp audit, costs and delay declared, delisted names present, rules-tried counter) + an LLM review layer. Each layer's catch rate is reported separately |
| 5 | Gold labels were chunk IDs, which change when chunk size changes | Gold labels are **quote spans in the source document**. A chunk is relevant if it contains the gold quote, so 256- vs 512-token chunks are compared fairly |
| 6 | Free price data silently drops delisted companies | Named as a known bias. The universe is defined by *who filed*, not by today's index membership. Coverage of delisted names is measured in notebook 01 and stated in the write-up |
| 7 | 100 questions alone cannot train a reranker | The fine-tune uses **synthetic queries** generated from chunks, with a leakage guard that excludes every held-out document. Moved to an optional stretch phase |
| 8 | ±0.1 uncertainty on recall@5 with ~50 held-out questions was not mentioned | Every metric is reported with a bootstrap 95% CI and n |
| 9 | Verifier proved a quote *exists*, not that it *supports* the claim | Two separate measures: **citation precision** (automatic, verbatim) and **faithfulness** (hand-graded) |
| 10 | Nothing for a reviewer to look at in 2 minutes | Each ship point produces a README with a headline table, a GIF of a cited answer, and the graveyard |
| 11 | When an after-hours filing becomes tradable was undefined | **Tradable rule:** an event can be acted on only at the first market *open strictly after* `published_at`. Never the same bar |

---

## Goals

1. Ingest SEC filings and answer questions about them with citations that quote the source verbatim.
2. Extract structured signals (guidance direction first; later new risk language and tone shift), each
   with its exact quote and the time the document became public.
3. Let agents propose and backtest rules on those signals inside a sandbox, and report how many survive
   correction for multiple testing.
4. Ship notebooks that reproduce every number in the write-up from a versioned data snapshot.

## Non-goals (v1)

Live trading, order execution, advice. Intraday data. Redistributing paid data. A general chatbot: the
system answers only from the filings it holds.

## Users

- **Primary:** the researcher (you).
- **Secondary:** a reviewer (interviewer or recruiter) who wants to check the work in about 2 minutes
  and reproduce any number in about 20.

## Success metrics

Targets are starting hypotheses; revise them after the first labelled run. Every number is reported with n
and a 95% CI.

| Metric | Target | How measured |
|---|---|---|
| Retrieval recall@5 | ≥ 0.85 | 100 hand-labelled questions with gold quote spans (50 dev / 50 held-out) |
| Citation precision | ≥ 0.95 | Share of cited quotes found verbatim (after whitespace normalisation) in the cited chunk |
| Faithfulness | ≥ 0.90 | Hand-graded: is each kept claim supported by its quote? |
| Answer correctness | ≥ 0.80 | Hand-graded on the same questions |
| Extraction accuracy | ≥ 0.90 | 100 hand-labelled signals, per signal type |
| Skeptic catch rate | 100% of planted flaws, **deterministic layer alone** | Planted lookahead, missing costs, survivorship, and p-hacking rules |
| Answer latency | median < 10 s | Over the question set |

---

## Data sources

| Need | Source | Notes |
|---|---|---|
| Company list | `sec.gov/files/company_tickers.json` | CIK ↔ ticker map |
| Filing metadata | `data.sec.gov/submissions/CIK##########.json` | form, items, accession, `acceptanceDateTime` |
| Documents | `sec.gov/Archives/edgar/data/...` | 10-K and 10-Q primary documents; EX-99.1 press release for 8-K Item 2.02 |
| Acceptance time | filing `-index.htm` "Accepted" field (US Eastern) | Cross-checked against the JSON in notebook 01 |
| Daily prices | **Open question, resolved in Phase 3** | Must have split/dividend adjustment and acceptable terms; delisted coverage measured |

**SEC fair access:** at most 10 requests per second (the ingester caps at 8), and a declared
`User-Agent` with a contact email set by the operator in `SEC_USER_AGENT`. The ingester refuses to run
without it.

### Point-in-time rules

- `published_at` is the EDGAR acceptance time, stored in UTC.
- Nothing derived from a document is visible to retrieval or to a backtest before its `published_at`.
- An event is tradable at the first market open strictly after `published_at`.
- Every backtest applies transaction costs and a delay. The skeptic fails any rule that doesn't state them.
- Every rule tried is recorded, and every reported result is corrected for the number tried.

---

## Architecture

```
            ┌──────────────┐   raw files + metadata   ┌───────────────────────────────┐
 EDGAR ───► │ Go ingester  │ ───────────────────────► │ Postgres 17 + pgvector        │
            │ rate-limited │                          │ documents, sections, chunks,  │
            └──────────────┘                          │ embeddings, signals, prices,  │
                                                      │ questions, runs, rules, jobs  │
            ┌──────────────┐  parse/chunk/embed       └──────┬─────────────┬──────────┘
            │ Python       │ ◄───────────────────────────────┘             │
            │ workers      │ ───── signals (verified) ─────────────────────┤
            └──────────────┘                                               │
            ┌──────────────┐  hybrid retrieval (as-of filtered)            │
 question ► │ RAG answerer │ ◄─────────────────────────────────────────────┤
            └──────────────┘                                               │
            ┌──────────────┐  rules → Docker sandbox → stats → skeptic     │
 agents ──► │ agent graph  │ ◄─────────────────────────────────────────────┘
            └──────────────┘
```

### RAG pipeline (each stage has one decision tested in a notebook)

| Stage | Decision to test |
|---|---|
| Fetch | De-duplication by content hash; acceptance-time source of truth |
| Parse | Rule-based Item sectioning (TOC-aware) vs fixed text; tables kept as pipe rows |
| Chunk | 256 vs 512 tokens; section-aware vs fixed windows; contextual header (company · form · section) |
| Embed | `bge-small-en-v1.5` (384-d) vs `bge-base-en-v1.5` (768-d), local on Apple Silicon |
| Index | HNSW vs exact search; one partial HNSW index per embedding model |
| Retrieve | Vector-only vs keyword-only vs hybrid (RRF); candidate count |
| Rerank + generate | With/without cross-encoder; Claude with a no-outside-knowledge prompt |
| Verify | Exact vs normalised match. A failed claim is dropped; if none survive, the answer says the filings don't answer the question |

Filing text is untrusted data. Prompts mark it as quoted material, and no agent action depends on it.

### Data model

| Table | Key fields | Purpose |
|---|---|---|
| `companies` | cik, ticker, name | Universe as it filed, not today's index membership |
| `documents` | accession_no, form, doc_kind, source_url, accepted_at, published_at, filed_date, content_hash, raw_path | One row per fetched document |
| `sections` | document_id, section, ordinal, text | Parsed sections; gold quotes live here |
| `chunks` | document_id, chunker, section, ordinal, text, token_count, char_start, char_end, published_at, tsv | Passages with offsets back into the section |
| `embeddings` | chunk_id, model, dim, vector | One row per chunk per model |
| `questions` | id, split, text, ticker, as_of, gold (quote spans), labelled_by | Eval set |
| `runs` | pipeline_config, git_sha, data_snapshot, metrics_json | Every experiment |
| `jobs` | kind, payload, status, attempts | Postgres-backed queue |
| `signals`, `prices`, `rules`, `backtests` | as in v1.0 | Added in Phases 3–4 |

---

## Agents and guardrails (Phases 4–5)

| Agent | Tools | Output | Rule it cannot break |
|---|---|---|---|
| Extractor | Read one chunk | Signal + quote | No quote, no signal |
| Verifier | Chunk + claim | pass / fail / unconfirmed | Unconfirmed never enters the store as fact |
| Hypothesis | Query the signal store (never prices) | Rule + rationale | Rationale saved before any data is touched |
| Coder | Write one backtest script | Script | Runs only in the sandbox |
| Skeptic | Rule, script, results, audit log | Verdict + reasons | Deterministic checks run first and can't be overruled by the LLM layer |
| Reporter | Verdicts | Dossier per survivor + graveyard | Failures get the same prominence as successes |

**Orchestration:** LangGraph. **Hand-written** (must be trusted): verifier, sandbox runner, statistics,
deterministic skeptic checks.

**Sandbox:** Docker with `--network none`, read-only root, a tmpfs scratch directory, no secrets, CPU,
memory, PID and time limits.

**Budgets:** caps on model calls and cost per run and per day, a step limit, and a kill switch. A run that
hits a limit stops and says so.

---

## Phases and gates

Each phase ends in something demoable. The next one starts only when the gate is met.
🚢 marks a **ship point**: the project is presentable on a resume if you stop there.

| Phase | Build | Notebooks | Gate |
|---|---|---|---|
| **0. Foundations** | Repo, Postgres + pgvector, migrations, CI, source terms documented | — | `make up && make test` green; terms checked |
| **1. Ingest and index (dev slice)** | Go ingester (~50 docs, 10 companies); parse, chunk ×3 configs, embed ×2 models, HNSW + full-text index; labelling CLI; **you** label 30 dev questions | 01, 02 | Publication-time audit passes; every chunk traces to a raw file; 2 chunk sizes × 2 embedders compared on dev questions |
| **2. Cited answers** 🚢 | Hybrid retrieval (RRF), cross-encoder rerank, Claude generation, verifier, refusal path; 100 questions (50/50); Go API + minimal ask page | 03 | recall@5, citation precision and faithfulness measured once on held-out, with CIs, and written down; README + GIF |
| **3. Scale and signals** 🚢 | Study universe ingestion; guidance-direction extractor + verifier; 100 hand-labelled signals; price cache; hand-written event study | 05, 06 | Extraction accuracy with CI; event study table and chart with n ≥ 30 per bucket, or an explicit "insufficient n" |
| **4. Sandbox and skeptic** | Docker sandbox, walk-forward splits, deflated Sharpe, Holm / BH correction, deterministic checks + LLM layer, planted-flaw suite | 07 (part 1) | Every planted flaw rejected by the deterministic layer alone |
| **5. Agents** 🚢 | Hypothesis + coder agents in LangGraph, budgets, kill switch, reporter | 07 (part 2) | 100 rules end to end without manual help; headline sentence filled in |
| **6. Stretch: fine-tune** | Reranker fine-tune on synthetic queries with a held-out leakage guard | 04 | Controlled comparison on held-out, published even if it loses |

## Honest-evaluation rules

- Dev / held-out split; held-out is touched once.
- Label before seeing model output. `labelled_by` is recorded on every label.
- n and a 95% CI beside every number. No claims from fewer than about 30 events.
- Notebooks show failure examples, not just averages.
- Cost and latency are tracked per run.
- Smoke-test questions written by the assistant are **never** used for reported metrics.

## Tech stack

| Concern | Choice |
|---|---|
| Ingestion, API | Go 1.25, pgx |
| Workers, ML, stats | Python 3.12 (uv), sentence-transformers on MPS, pandas / scipy / statsmodels, PyTorch |
| Store and queue | Postgres 17 + pgvector (Docker) |
| LLM | Claude API (Phase 2+), every call cached and budgeted |
| Notebooks | Jupytext percent `.py` files as the source of truth, executed to `.ipynb` / `reports/` |
| Tests, CI | pytest, `go test`, GitHub Actions with a pgvector service container |

## Risks

| Risk | Mitigation |
|---|---|
| EDGAR throttling | 8 req/s cap, backoff honours `Retry-After`, every raw response cached on disk |
| HTML parsing errors corrupt chunks | Raw file kept per document; section coverage and a hand-check sample in notebook 01 |
| Weak labels | Gold = quote spans, validated verbatim against sections; label before seeing outputs |
| Underpowered event study | Study universe in Phase 3; n stated; "insufficient n" is a valid result |
| Survivorship in prices | Universe from filers; delisted coverage measured and stated |
| Sandbox escape | No network, read-only fs, limits, no secrets |
| Model costs | Per-run and per-day caps; caching |
| Mistaken for advice | Every output labelled research |

## Findings log

What real data changed, in order. Each entry names the evidence.

| Date | Finding | Evidence | Consequence |
|---|---|---|---|
| 7 Nov 2026 | Submissions-JSON `acceptanceDateTime` is true UTC; it matches the index page to the second | Notebook 01, 49/49 filings | Kept the index page as the source of truth; the check stays as a regression test |
| 7 Nov 2026 | Most filings arrive outside market hours (8-Ks: 12 pre-market, 8 after close, 0 during the session) | Notebook 01 | Confirms the "first open strictly after `published_at`" rule matters |
| 7 Nov 2026 | XOM now maps to a new CIK (0002115436) with no 10-K yet; one 10-Q still carries the old CIK's accession prefix | Ingester dry run | **Phase 3:** the universe needs a CIK history per company, not today's ticker map |
| 7 Nov 2026 | Parser v1 marked every required section "found" while mislabelling DAL (TOC line as MD&A) and dropping text | Notebook 01 hand-check | Added size checks. v2/v3: TOC detection, first occurrence after the TOC, body-derived Part II, unlabelled text kept |
| 7 Nov 2026 | v2 broke PFE (bare "Item 1." TOC lines); the new size check caught it | Notebook 01 | v3 extends the TOC through bare headings; regression tests for DAL, PFE and JPM layouts |
| 7 Nov 2026 | JPM files cross-reference 10-Ks and 10-Qs; MD&A is not under an Item heading | Notebook 01 | Known limitation: text kept (`unlabelled` / `exhibits`), section filters miss it. Title-based parser to be measured in Phase 3 |
| 7 Nov 2026 | Many 10-Q Risk Factors just say "no material changes" | Notebook 01 (12 of 18) | **Phase 3:** "new risk language" must diff against the latest 10-K |

## Open questions

- Which free daily price source has adequate adjustments, delisted coverage and acceptable terms? (Phase 3)
- Which Claude model for generation vs extraction, given cost? (Phase 2 — measure, don't assume)
- Are transcripts available from a permitted source? (Deferred; filings first.)
