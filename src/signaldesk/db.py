"""Database connection and a minimal migration runner."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress

import psycopg
from pgvector.psycopg import register_vector

from signaldesk.config import REPO_ROOT, settings

MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"


@contextmanager
def connect(url: str | None = None) -> Iterator[psycopg.Connection]:
    # autocommit=True so every `with conn.transaction():` block is a real, durable transaction. With
    # autocommit off, the first SELECT opens an outer transaction and each block becomes a savepoint
    # inside it, so nothing is saved until the connection closes cleanly. A long batch job that is
    # killed then loses all of its work (this happened: ~33k embeddings rolled back).
    with psycopg.connect(url or settings.database_url, autocommit=True) as conn:
        # The vector type doesn't exist until the first migration creates the extension.
        with suppress(psycopg.ProgrammingError):
            register_vector(conn)
        yield conn


def migrate(url: str | None = None) -> list[str]:
    """Apply every db/migrations/*.sql not yet recorded. Returns the names applied."""
    applied: list[str] = []
    with connect(url) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            applied.append(path.name)
    return applied
