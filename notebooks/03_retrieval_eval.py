# ---
# jupyter:
#   jupytext:
#     formats: py:percent
#   kernelspec:
#     display_name: Python 3
#     name: python3
# ---

# %% [markdown]
# # 03 · Retrieval and cited answers
#
# **Questions:** Does hybrid search beat vector-only and keyword-only? Do "latest"-aware filtering and a
# reranker help? How good are the final cited answers?
#
# **Protocol:**
# 1. Tune on the **dev** split only (50 questions), in two stages so the grid stays affordable.
# 2. Freeze the winning configuration to `reports/03_frozen_config.json`.
# 3. Score the **held-out** split (45 answerable + 10 can't-answer) **once**. A lock file records that
#    it was scored; re-scoring needs an explicit `SIGNALDESK_RESCORE_HELDOUT=1`, and the notebook says so.
#
# Every number has n and a 95% bootstrap CI. "Strict" means the gold passage from the labelled filing;
# "lenient" also counts the identical sentence found in another filing from the same company.

# %%
import json
import os
import subprocess
import time
import warnings
from datetime import UTC, datetime

import matplotlib.pyplot as plt
import pandas as pd

from signaldesk.config import REPO_ROOT
from signaldesk.db import connect
from signaldesk.evaluation import bootstrap_ci, evaluate, load_questions
from signaldesk.search import RERANKERS, SearchConfig, search

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")
REPORTS = REPO_ROOT / "reports"
REPORTS.mkdir(exist_ok=True)
pd.set_option("display.width", 180, "display.max_colwidth", 90)

dev = [q for q in load_questions(REPO_ROOT / "eval/questions/dev.jsonl") if q.split == "dev"]
heldout = load_questions(REPO_ROOT / "eval/questions/heldout.jsonl")
ho_answerable = [q for q in heldout if q.answerable]
ho_cna = [q for q in heldout if not q.answerable]
print(f"dev {len(dev)} · held-out {len(ho_answerable)} answerable + {len(ho_cna)} can't-answer")


def have_model(hf_name: str) -> bool:
    from huggingface_hub import try_to_load_from_cache
    return isinstance(try_to_load_from_cache(hf_name, "model.safetensors"), str)


# bge-reranker-v2-m3 costs ~10 s per question on an M4 (vs ~3 s for base), so it cannot meet the 10 s answer
# budget. It is opt-in here: SIGNALDESK_RERANKERS=base,v2-m3. Its timed comparison is in 03_reranker_check.csv.
wanted = os.environ.get("SIGNALDESK_RERANKERS", "base").split(",")
rerankers = [k for k in wanted if k in RERANKERS and have_model(RERANKERS[k])]
print("rerankers in this run:", rerankers)


def run(conn, questions, cfg):
    t0 = time.perf_counter()
    df = evaluate(conn, questions, lambda q: [h.chunk_id for h in search(
        conn, q.text, cfg, tickers=[q.ticker] if q.ticker else None, as_of=q.as_of)])
    ms = (time.perf_counter() - t0) / max(len(questions), 1) * 1000
    row = {"config": cfg.label(), "n": len(df), "ms_per_query": round(ms)}
    for m in ("recall@5", "recall@5_lenient", "mrr", "ndcg@10"):
        mean, lo, hi = bootstrap_ci(df[m].tolist())
        row[m], row[f"{m}_lo"], row[f"{m}_hi"] = mean, lo, hi
    return row, df


# %% [markdown]
# ## Stage A: retrieval mode, chunker, embedder, recency (no reranker)

# %%
stage_a = []
base = SearchConfig()
with connect() as conn:
    for chunker in ("sec256", "sec512"):
        for model in ("bge-small", "bge-base"):
            for mode in ("vector", "keyword", "hybrid"):
                if mode == "keyword" and model == "bge-base":
                    continue
                for recency in ("off", "auto"):
                    cfg = base.but(chunker=chunker, model=model, mode=mode, recency=recency)
                    row, _ = run(conn, dev, cfg)
                    stage_a.append({**row, "_cfg": cfg})
a = pd.DataFrame(stage_a).sort_values(["recall@5", "mrr"], ascending=False)
a.drop(columns="_cfg").to_csv(REPORTS / "03_stage_a.csv", index=False)
a[["config", "n", "recall@5", "recall@5_lo", "recall@5_hi", "recall@5_lenient", "mrr", "ms_per_query"]].round(3)

# %% [markdown]
# ### Hybrid vs vector-only vs keyword-only
#
# Mean over every chunker × embedder × recency setting, so the comparison isn't one lucky setting.

# %%
a["mode"] = [c.mode for c in a["_cfg"]]
a.groupby("mode")[["recall@5", "mrr"]].mean().round(3)

# %% [markdown]
# ## Stage B: rerankers on the two best Stage A configurations

# %%
stage_b = []
with connect() as conn:
    for cfg in list(a["_cfg"].head(2)):
        stage_b.append({**run(conn, dev, cfg)[0], "_cfg": cfg})
        for rr in rerankers:
            c = cfg.but(rerank=True, reranker=rr)
            stage_b.append({**run(conn, dev, c)[0], "_cfg": c})
b = pd.DataFrame(stage_b).sort_values(["recall@5", "mrr"], ascending=False)
b.drop(columns="_cfg").to_csv(REPORTS / "03_stage_b.csv", index=False)
b[["config", "n", "recall@5", "recall@5_lo", "recall@5_hi", "recall@5_lenient", "mrr", "ms_per_query"]].round(3)

# %% [markdown]
# ## Freeze
#
# The winner is the best dev recall@5 (ties broken by MRR). Overlapping intervals mean the choice is a
# reasonable default, not proof that it beats the runner-up.

# %%
frozen: SearchConfig = b.iloc[0]["_cfg"]
(REPORTS / "03_frozen_config.json").write_text(json.dumps(frozen.to_dict(), indent=2))
print("frozen:", frozen.label())

fig, ax = plt.subplots(figsize=(8, 0.4 * len(b) + 1))
bb = b.sort_values("recall@5")
ax.errorbar(bb["recall@5"], bb["config"], xerr=[bb["recall@5"] - bb["recall@5_lo"], bb["recall@5_hi"] - bb["recall@5"]],
            fmt="o", capsize=3)
ax.axvline(0.85, ls="--", lw=1, color="grey")
ax.set_xlim(0, 1)
ax.set_xlabel(f"dev recall@5 (n = {len(dev)}, 95% bootstrap CI); dashed line = 0.85 target")
fig.tight_layout()
fig.savefig(REPORTS / "03_dev_stage_b.png", dpi=150)

# %% [markdown]
# ## Held-out: scored once

# %%
lock = REPORTS / "03_heldout_result.json"
rescore = os.environ.get("SIGNALDESK_RESCORE_HELDOUT") == "1"
if lock.exists() and not rescore:
    held = json.loads(lock.read_text())
    print("Held-out was already scored on", held["scored_at"], "with", held["config"])
    print("Showing the recorded result. Set SIGNALDESK_RESCORE_HELDOUT=1 to re-score (and say so in the write-up).")
else:
    with connect() as conn:
        row, ho_df = run(conn, ho_answerable, frozen)
    ho_df.to_csv(REPORTS / "03_heldout_per_question.csv", index=False)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                         cwd=REPO_ROOT).stdout.strip()
    held = {"scored_at": datetime.now(UTC).isoformat(timespec="seconds"), "git_sha": sha,
            "config": frozen.label(), "rescored": bool(lock.exists()), **{k: v for k, v in row.items() if k != "config"}}
    lock.write_text(json.dumps(held, indent=2))
    with connect() as conn:
        conn.execute("INSERT INTO runs (kind, pipeline_config, git_sha, finished_at, metrics_json) "
                     "VALUES ('retrieval_heldout', %s, %s, now(), %s)",
                     (json.dumps(frozen.to_dict()), sha, json.dumps(held)))
pd.Series(held)

# %% [markdown]
# ## Cited answers on held-out
#
# Runs only when an Anthropic credential is configured. Each model call is cached in Postgres, so a rerun
# costs nothing. Automatic metrics:
#
# - **Citation precision**: share of the model's quotes that the verifier finds in the cited passage.
# - **Answered rate** on answerable questions, and **refusal rate** on can't-answer questions.
# - **Latency**: median seconds per answer (retrieval + generation + verification).
#
# Faithfulness and correctness need a human: the cell exports a grading sheet.

# %%
have_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
if not have_key:
    print("No ANTHROPIC_API_KEY set: skipping answer generation. Add it to .env and rerun this cell.")
else:
    from signaldesk.answer import ask

    rows = []
    with connect() as conn:
        for q in heldout:
            a_ = ask(conn, q.text, cfg=frozen, tickers=[q.ticker] if q.ticker else None, as_of=q.as_of)
            rows.append({
                "id": q.id, "answerable": q.answerable, "status": a_.status, "answer": a_.answer,
                "n_claims": len(a_.claims), "n_verified": len(a_.kept),
                "quotes": " || ".join(c.quote for c in a_.claims),
                "gold_answer": q.gold_answer or "", "latency_s": a_.latency_ms / 1000,
                "cached": bool(a_.llm and a_.llm.cached),
            })
    ans = pd.DataFrame(rows)
    ans.to_csv(REPORTS / "03_answers_for_grading.csv", index=False)
    claims = ans["n_claims"].sum()
    cp = ans["n_verified"].sum() / claims if claims else float("nan")
    answered = (ans[ans.answerable].status == "answered").mean()
    refused = (ans[~ans.answerable].status != "answered").mean()
    print(f"citation precision {cp:.3f} ({ans['n_verified'].sum()}/{claims} quotes verified)")
    print(f"answered {answered:.2f} of {ans.answerable.sum()} answerable · refused {refused:.2f} of {(~ans.answerable).sum()} can't-answer")
    print(f"median latency {ans.latency_s.median():.1f}s (cached calls are faster)")
    ans[["id", "status", "n_claims", "n_verified", "answer"]]

# %% [markdown]
# ## Failure examples (held-out, frozen config)
#
# Strict misses, with what came back instead. These feed the Phase 3 parser and search work.

# %%
per_q = REPORTS / "03_heldout_per_question.csv"
if per_q.exists():
    hq = pd.read_csv(per_q)
    miss = hq[hq["recall@5"] == 0]
    text = {q.id: q.text for q in ho_answerable}
    print(f"{len(miss)} of {len(hq)} held-out questions missed at k=5")
    for r_ in miss.itertuples():
        print("✗", r_.question_id, text.get(r_.question_id, ""))
