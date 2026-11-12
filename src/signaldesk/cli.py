"""signaldesk command line."""

from __future__ import annotations

import json
import logging
import textwrap
from datetime import datetime

import typer

from signaldesk import db, pipeline
from signaldesk.chunk import CHUNKERS
from signaldesk.embed import MODELS

app = typer.Typer(no_args_is_help=True, add_completion=False)
db_app = typer.Typer(no_args_is_help=True)
label_app = typer.Typer(no_args_is_help=True, help="Helpers for writing gold labels.")
app.add_typer(db_app, name="db")
app.add_typer(label_app, name="label")


@app.callback()
def _main(verbose: bool = typer.Option(True, "--verbose/--quiet")):
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")


@db_app.command("migrate")
def migrate():
    """Apply pending SQL migrations."""
    applied = db.migrate()
    typer.echo("applied: " + (", ".join(applied) if applied else "nothing (up to date)"))


@app.command()
def parse():
    """Parse raw documents into sections."""
    with db.connect() as conn:
        typer.echo(f"parsed {pipeline.parse_documents(conn)} documents")


@app.command()
def chunk(chunker: list[str] = typer.Option(list(CHUNKERS), help="chunker config(s)")):
    """Chunk sections with one or more chunker configs."""
    with db.connect() as conn:
        for c in chunker:
            typer.echo(f"{c}: {pipeline.chunk_sections(conn, c)} new chunks")


@app.command()
def embed(
    model: list[str] = typer.Option(list(MODELS), help="embedding model key(s)"),
    chunker: list[str] = typer.Option(list(CHUNKERS), help="chunker config(s)"),
):
    """Embed chunks for every (model, chunker) pair."""
    with db.connect() as conn:
        for m in model:
            for c in chunker:
                typer.echo(f"{m} × {c}: {pipeline.embed_chunks(conn, m, c)} new embeddings")


@app.command()
def index():
    """parse → chunk (all configs) → embed (all models)."""
    with db.connect() as conn:
        pipeline.parse_documents(conn)
        for c in CHUNKERS:
            pipeline.chunk_sections(conn, c)
            for m in MODELS:
                pipeline.embed_chunks(conn, m, c)
    typer.echo("index up to date")


def _show(conn, ids: list[int], width: int = 100, max_chars: int = 600):
    rows = {r[0]: r[1:] for r in conn.execute(
        """SELECT c.id, co.ticker, d.form, d.accession_no, c.section, d.published_at, c.text
           FROM chunks c JOIN documents d ON d.id = c.document_id
           JOIN companies co ON co.id = c.company_id WHERE c.id = ANY(%s)""", (ids,))}
    for rank, cid in enumerate(ids, 1):
        t, form, acc, sec, pub, text = rows[cid]
        typer.secho(f"\n#{rank}  chunk {cid}  {t} {form} {acc}  [{sec}]  published {pub:%Y-%m-%d %H:%M}Z",
                    bold=True)
        body = text if len(text) <= max_chars else text[:max_chars] + " …"
        typer.echo(textwrap.fill(body, width=width, replace_whitespace=False))


FROZEN = "reports/03_frozen_config.json"


def _config(path: str = FROZEN):
    """The search config chosen in notebook 03 (defaults if the notebook hasn't run)."""
    from signaldesk.config import REPO_ROOT
    from signaldesk.search import SearchConfig

    p = REPO_ROOT / path
    return SearchConfig(**json.loads(p.read_text())) if p.exists() else SearchConfig()


def _search_and_show(query: str, tickers: list[str] | None, as_of: str | None, cfg) -> None:
    from signaldesk.search import search as run_search

    with db.connect() as conn:
        hits = run_search(conn, query, cfg, tickers=tickers,
                          as_of=datetime.fromisoformat(as_of) if as_of else None)
        if not hits:
            typer.echo("No passages matched (check the ticker and as-of date).")
        _show(conn, [h.chunk_id for h in hits])


@app.command()
def search(
    query: str,
    ticker: list[str] = typer.Option(None),
    as_of: str = typer.Option(None, help="ISO time; only documents published at or before it"),
    mode: str = typer.Option(None, help="vector | keyword | hybrid (default: frozen config)"),
    k: int = 5,
):
    """Search the index with the same pipeline `ask` uses, and print passages."""
    cfg = _config().but(k=k, **({"mode": mode} if mode else {}))
    _search_and_show(query, ticker, as_of, cfg)


@label_app.command("find")
def label_find(query: str, ticker: list[str] = typer.Option(None), k: int = 8):
    """Find candidate passages for a question you are labelling (the pipeline `ask` uses).

    Copy the exact sentence(s) that answer the question into the "quote" field of your JSONL."""
    _search_and_show(query, ticker, None, _config().but(k=k))


@label_app.command("check")
def label_check(path: str):
    """Validate a questions JSONL file: every gold quote must exist in its filing."""
    from signaldesk.evaluation import load_questions, resolve_gold

    qs = load_questions(path)
    bad = 0
    with db.connect() as conn:
        for q in qs:
            _, errors = resolve_gold(conn, q)
            for e in errors:
                typer.secho(e, fg="red")
            bad += bool(errors)
    splits: dict[str, int] = {}
    for q in qs:
        splits[q.split] = splits.get(q.split, 0) + 1
    typer.echo(f"{len(qs)} questions {splits}; {bad} with errors")
    raise typer.Exit(1 if bad else 0)


@label_app.command("load")
def label_load(path: str):
    """Upsert a validated questions file into the questions table."""
    from signaldesk.evaluation import load_questions

    qs = load_questions(path)
    with db.connect() as conn, conn.transaction():
        for q in qs:
            conn.execute(
                """INSERT INTO questions (id, split, text, ticker, as_of, gold, gold_answer, labelled_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET split=EXCLUDED.split, text=EXCLUDED.text,
                     ticker=EXCLUDED.ticker, as_of=EXCLUDED.as_of, gold=EXCLUDED.gold,
                     gold_answer=EXCLUDED.gold_answer, labelled_by=EXCLUDED.labelled_by""",
                (q.id, q.split, q.text, q.ticker, q.as_of,
                 json.dumps([g.__dict__ for g in q.gold]), q.gold_answer, q.labelled_by),
            )
    typer.echo(f"loaded {len(qs)} questions")


@app.command("ask")
def ask_cmd(
    question: str,
    ticker: list[str] = typer.Option(None),
    as_of: str = typer.Option(None, help="ISO time; only filings published at or before it"),
    config: str = typer.Option(FROZEN, help="search config chosen in notebook 03"),
):
    """Answer a question from the filings with verified quotes (needs ANTHROPIC_API_KEY)."""
    from signaldesk.answer import ask

    cfg = _config(config)
    with db.connect() as conn:
        a = ask(conn, question, cfg=cfg, tickers=ticker, as_of=datetime.fromisoformat(as_of) if as_of else None)
    colour = {"answered": "green"}.get(a.status, "yellow")
    typer.secho(f"\n[{a.status}] {a.answer}", fg=colour, bold=True)
    for c in a.claims:
        mark = "✓" if c.ok else "✗"
        typer.echo(f"  {mark} {c.passage_id} ({c.result}): \"{c.quote}\"")
    typer.echo("\nSources:")
    for p in a.passages:
        typer.echo(f"  {p.pid} {p.ticker} {p.form} [{p.section}] {p.published_at:%Y-%m-%d}  {p.source_url}")


if __name__ == "__main__":
    app()
