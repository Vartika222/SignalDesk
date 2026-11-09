# Signal Desk

Cited, point-in-time signals from SEC filings, with honest evaluation.

> From **N** filings I extracted **M** signals and proposed **K** rules; **J** survived correction
> for multiple testing. *(Filled in at Phase 5.)*

Research only. Not investment advice.

**Status:** Phase 1 (ingest and index) in progress. Plan and gates: [docs/PRD.md](docs/PRD.md).

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
  retrieval.py   vector (HNSW), keyword (full text), RRF; all as-of filtered
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
cp .env.example .env        # then set SEC_USER_AGENT="Your Name you@example.com"
make up                     # Postgres + pgvector, migrations
make test                   # Go + Python tests (first run downloads bge tokenizer/models)
make ingest-dry             # what the dev slice would fetch
make ingest-dev             # ~50 filings from 10 companies → data/raw + documents table
make index                  # parse → 3 chunkers → 2 embedders
uv run signaldesk search "Did management lower guidance?" --ticker NKE
make notebooks              # executes notebooks into reports/
```

## Data and terms

Filings come from SEC EDGAR under its fair-access policy: a declared User-Agent and at most 10
requests per second (we cap at 8). Raw files are cached locally and not redistributed.
