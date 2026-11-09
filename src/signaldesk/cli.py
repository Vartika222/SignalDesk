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


@app.command()
def search(
    query: str,
    ticker: list[str] = typer.Option(None),
    as_of: str = typer.Option(None, help="ISO time; only documents published at or before it"),
    mode: str = typer.Option("hybrid", help="vector | keyword | hybrid"),
    model: str = "bge-base",
    chunker: str = "sec512",
    k: int = 5,
    exact: bool = typer.Option(False, help="exact vector search (no HNSW)"),
):
    """Search the index and print passages."""
    from signaldesk import retrieval as r

    with db.connect() as conn:
        cids = r.company_ids_for(conn, ticker) if ticker else None
        when = datetime.fromisoformat(as_of) if as_of else None
        kw = dict(chunker=chunker, company_ids=cids, as_of=when)
        if mode == "vector":
            hits = r.vector_search(conn, query, model=model, k=k, exact=exact, **kw)
        elif mode == "keyword":
            hits = r.keyword_search(conn, query, k=k, **kw)
        else:
            hits = r.rrf(r.vector_search(conn, query, model=model, k=50, **kw),
                         r.keyword_search(conn, query, k=50, **kw), limit=k)
        _show(conn, [h.chunk_id for h in hits])


@label_app.command("find")
def label_find(query: str, ticker: list[str] = typer.Option(None), k: int = 8):
    """Find candidate passages for a question you are labelling (hybrid search, wide net).

    Copy the exact sentence(s) that answer the question into the "quote" field of your JSONL."""
    search(query, ticker=ticker, as_of=None, mode="hybrid", model="bge-base",
           chunker="sec512", k=k, exact=False)


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


if __name__ == "__main__":
    app()
