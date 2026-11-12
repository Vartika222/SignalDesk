# Labelled questions

One JSON object per line. Gold labels are **quotes**, not chunk ids, so every chunker is judged
against the same truth.

Illustrative shape only (the accession number and quote are placeholders):

```json
{"id": "q001", "split": "dev", "ticker": "ACME", "as_of": "2025-07-01T00:00:00+00:00",
 "text": "Did Acme expect revenue to decline next quarter?",
 "gold": [{"accession_no": "0000000000-25-000000", "quote": "we expect first quarter revenue to be down mid-single digits"}],
 "gold_answer": "Yes: down mid-single digits.",
 "labelled_by": "vartika"}
```

| Field | Rule |
|---|---|
| `split` | `dev` (tune on it freely) or `heldout` (scored once, in notebook 03). `smoke` is reserved for assistant-written pipeline checks and is never reported |
| `quote` | Copied exactly from the filing; whitespace, curly quotes and dashes are forgiven. One sentence is usually enough |
| `as_of` | Retrieval only sees documents published at or before it. Set it to the labelling date at least: "latest" questions are filtered to the newest filing as of this time, so without it a later download would hide the gold filing |
| `labelled_by` | Your name. Required |

## Workflow

1. Write the question **before** searching, so the system's output can't shape it.
2. Find the answer: `uv run signaldesk label find "your question" --ticker NKE`, or read the filing on sec.gov.
3. Copy the exact sentence into `quote` and its accession number into `accession_no`.
4. Validate: `uv run signaldesk label check eval/questions/dev.jsonl` (each quote must exist in its filing).
5. Aim for a mix: guidance, risk factors, figures in tables, yes/no, and some questions the filings
   **can't** answer. Phase 2 needs those to test refusals.

Targets: 30 dev for Phase 1, then 100 total (50 dev / 50 held-out) for Phase 2.
