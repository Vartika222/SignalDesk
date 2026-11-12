# Signal Desk

Cited, point-in-time signals from SEC filings, with honest evaluation.

> From **N** filings I extracted **M** signals and proposed **K** rules; **J** survived correction
> for multiple testing. *(Filled in at Phase 5.)*

Research only. Not investment advice.

**Status:** Phase 2 (cited answers) in progress. Plan, gates and findings log: [docs/PRD.md](docs/PRD.md).

## Results so far

| Measure | Value | n | Where |
|---|---|---|---|
| Held-out retrieval recall@5 (scored once) | **0.62** [0.49, 0.76] | 45 | notebook 03 |
| Held-out MRR / nDCG@10 | 0.43 / 0.57 | 45 | notebook 03 |
| Dev recall@5, Phase 1 baseline → frozen config | 0.43 → 0.54 | 50 | notebooks 02, 03 |
| Target | 0.85 | | PRD |
| Citation precision, refusal rate, faithfulness | not yet measured (needs an API key) | | notebook 03 |

The frozen pipeline: 512-token section-aware chunks, bge-small embeddings, hybrid search fused with RRF,
"latest filing" filtering when the question asks for it, then bge-reranker-base over 50 candidates.

## What's interesting here

- **Point-in-time by construction.** Every row carries `published_at`, the SEC acceptance time
  taken from the filing index page. Every retrieval is filtered by it, so a backtest can't read its
  own future. Notebook 01 audits the timestamps, including whether the submissions feed's
  `acceptanceDateTime` is really UTC.
- **Gold labels are quotes, not chunk ids**, so 256- and 512-token chunkers are scored against the
  same truth.
- **One Postgres for everything.** pgvector holds two embedding models side by side in one
  dimensionless column, each with its own partial HNSW index on a cast expression.
- **The Go/Python split follows a real boundary.** Go does the rate-limited, retrying,
  network-bound ingestion; Python does parsing, ML and statistics.
- **Every number has n and a CI.** The held-out split is touched once.

## Layout

```
ingest/      Go: EDGAR client (≤8 req/s, retries, Retry-After), filing selection, raw store
src/signaldesk/
  parse.py       HTML → text (tables kept as rows) → Item sections (TOC-aware)
  chunk.py       paragraph-aware and fixed-window chunkers; chunks are exact substrings
  embed.py       bge-small / bge-base, local on Apple Silicon (MPS)
  retrieval.py   vector (HNSW), keyword (full text), RRF; as-of and latest-filing filters
  search.py      the full pipeline: filter → hybrid → RRF → cross-encoder rerank
  answer.py      Claude answers from retrieved passages only, as JSON claims with quotes
  verify.py      hand-written quote verifier; unverified claims are dropped, not shown
  llm.py         cached, budgeted Claude calls with server-side refusal fallback
  evaluation.py  quote-span gold, recall@k / MRR / nDCG, bootstrap CIs
  pipeline.py    idempotent parse → chunk → embed batches
  cli.py         `signaldesk …`
db/migrations/ SQL schema
notebooks/     jupytext sources (01 data audit, 02 chunking & embeddings, …)
eval/          tickers, labelled questions
reports/       exported tables and figures cited by the write-up
docs/PRD.md    requirements, phases and gates
```

## Run it

Prerequisites: Docker, Go 1.25, [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env        # set SEC_USER_AGENT; ANTHROPIC_API_KEY for `ask` and answer scoring
make up                     # Postgres + pgvector, migrations
make test                   # Go + Python tests (first run downloads bge tokenizer/models)
make ingest-dry             # what the dev slice would fetch
make ingest-dev             # ~50 filings from 10 companies → data/raw + documents table
make index                  # parse → 3 chunkers → 2 embedders
uv run signaldesk search "Did management lower guidance?" --ticker NKE
uv run signaldesk ask "What revenue outlook did Delta give for the next quarter?" --ticker DAL
make notebooks              # executes notebooks into reports/
```

## Data and terms

Filings come from SEC EDGAR under its fair-access policy: a declared User-Agent and at most 10
requests per second (we cap at 8). Raw files are cached locally and not redistributed.
