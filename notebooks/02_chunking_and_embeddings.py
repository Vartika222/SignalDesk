# ---
# jupyter:
#   jupytext:
#     formats: py:percent
#   kernelspec:
#     display_name: Python 3
#     name: python3
# ---

# %% [markdown]
# # 02 · Chunking and embeddings
#
# **Question:** Which chunk size and embedding model retrieve best on our own labelled questions?
#
# Grid: 3 chunkers (`sec256`, `sec512`, `fixed512`) × 2 embedders (`bge-small`, `bge-base`) ×
# 3 retrieval modes (vector, keyword, hybrid RRF). Keyword search doesn't depend on the embedder,
# so it appears once per chunker.
#
# **Rules:** this notebook only touches the **dev** split. Held-out questions are scored once, in
# notebook 03, after every choice here is frozen. Every number carries n and a bootstrap 95% CI.

# %%
import json
import subprocess
import time
import warnings

import matplotlib.pyplot as plt
import pandas as pd

from signaldesk import retrieval as r
from signaldesk.chunk import CHUNKERS
from signaldesk.config import REPO_ROOT
from signaldesk.db import connect
from signaldesk.embed import MODELS
from signaldesk.evaluation import bootstrap_ci, evaluate, load_questions
from signaldesk.search import SearchConfig, search

REPORTS = REPO_ROOT / "reports"
REPORTS.mkdir(exist_ok=True)
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

dev_path = REPO_ROOT / "eval/questions/dev.jsonl"
if dev_path.exists():
    questions = [q for q in load_questions(dev_path) if q.split == "dev"]
    REPORTABLE = True
else:
    questions = load_questions(REPO_ROOT / "eval/questions/smoke.jsonl")
    REPORTABLE = False
    print("⚠ No eval/questions/dev.jsonl yet: running on SMOKE questions written by the assistant.")
    print("  These check that the pipeline works. They are NOT reportable results.")
print(f"{len(questions)} questions")

# %%
def make_retriever(conn, mode, chunker, model):
    # The shared pipeline with no reranker and no recency filter: the Phase 1 baseline.
    cfg = SearchConfig(chunker=chunker, model=model, mode=mode, k=10)
    return lambda q: [h.chunk_id for h in search(
        conn, q.text, cfg, tickers=[q.ticker] if q.ticker else None, as_of=q.as_of)]


results, per_question = [], []
with connect() as conn:
    for chunker in CHUNKERS:
        for model in MODELS:
            for mode in ("vector", "keyword", "hybrid"):
                if mode == "keyword" and model != next(iter(MODELS)):
                    continue
                t0 = time.perf_counter()
                df = evaluate(conn, questions, make_retriever(conn, mode, chunker, model))
                ms = (time.perf_counter() - t0) / max(len(questions), 1) * 1000
                row = {"chunker": chunker, "model": model if mode != "keyword" else "—",
                       "mode": mode, "n": len(df), "ms_per_query": round(ms, 1)}
                for m in ("recall@5", "mrr", "ndcg@10"):
                    mean, lo, hi = bootstrap_ci(df[m].tolist())
                    row[m], row[f"{m}_lo"], row[f"{m}_hi"] = mean, lo, hi
                results.append(row)
                per_question.append(df.assign(chunker=chunker, model=row["model"], mode=mode))

grid = pd.DataFrame(results).sort_values("recall@5", ascending=False)
grid.to_csv(REPORTS / "02_grid.csv", index=False)
grid[["chunker", "model", "mode", "n", "recall@5", "recall@5_lo", "recall@5_hi", "mrr", "ndcg@10",
      "ms_per_query"]].round(3)

# %% [markdown]
# ## Chart: recall@5 with 95% CIs
#
# Overlapping intervals mean the data can't tell those settings apart yet. With about 30 dev
# questions, expect intervals roughly ±0.15 wide. A choice made inside that noise should be stated
# as a default, not as a finding.

# %%
g = grid.copy()
g["label"] = g.chunker + " · " + g.model + " · " + g["mode"]
g = g.sort_values("recall@5")
fig, ax = plt.subplots(figsize=(8, 0.35 * len(g) + 1))
ax.errorbar(g["recall@5"], g.label, xerr=[g["recall@5"] - g["recall@5_lo"], g["recall@5_hi"] - g["recall@5"]],
            fmt="o", capsize=3)
ax.set_xlim(0, 1.02)
ax.set_xlabel(f"recall@5 (n = {len(questions)} {'dev' if REPORTABLE else 'SMOKE'} questions, 95% bootstrap CI)")
ax.axvline(0.85, ls="--", lw=1, color="grey")
ax.text(0.85, -0.9, " target 0.85", color="grey", fontsize=8)
fig.tight_layout()
fig.savefig(REPORTS / "02_recall_at_5.png", dpi=150)

# %% [markdown]
# ## HNSW vs exact search
#
# Approximate search should lose almost nothing at this index size. Here we measure overlap@10
# between HNSW and exact results for every question, per embedder.

# %%
overlap = []
with connect() as conn:
    for model in MODELS:
        for q in questions:
            kw = dict(model=model, chunker="sec512", k=10)
            a = {h.chunk_id for h in r.vector_search(conn, q.text, **kw)}
            b = {h.chunk_id for h in r.vector_search(conn, q.text, exact=True, **kw)}
            overlap.append({"model": model, "overlap@10": len(a & b) / max(len(b), 1)})
pd.DataFrame(overlap).groupby("model")["overlap@10"].agg(["mean", "min", "count"])

# %% [markdown]
# ## Failures
#
# Questions the best setting misses entirely in the top 5, with what it returned instead. Reading
# these says more than the averages do.

# %%
best = grid.iloc[0]
pq = pd.concat(per_question)
miss = pq[(pq.chunker == best.chunker) & (pq.model == best.model) & (pq["mode"] == best["mode"])
          & (pq["recall@5"] == 0)]
qtext = {q.id: q.text for q in questions}
with connect() as conn:
    for row in miss.itertuples():
        print(f"✗ {row.question_id}: {qtext[row.question_id]}")
        for cid in row.retrieved[:2]:
            sec, text = conn.execute("SELECT section, left(text, 200) FROM chunks WHERE id=%s", (cid,)).fetchone()
            print(f"    got [{sec}] {text!r}")
print(f"{len(miss)} of {len(questions)} questions missed by the best setting")

# %% [markdown]
# ## Run record

# %%
sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                     cwd=REPO_ROOT).stdout.strip() or None
with connect() as conn:
    conn.execute(
        "INSERT INTO runs (kind, pipeline_config, git_sha, finished_at, metrics_json) VALUES (%s,%s,%s,now(),%s)",
        ("retrieval_grid_dev" if REPORTABLE else "retrieval_grid_smoke",
         json.dumps({"chunkers": list(CHUNKERS), "models": list(MODELS)}), sha,
         grid.to_json(orient="records")),
    )
print("best:", best[["chunker", "model", "mode", "recall@5"]].to_dict())
