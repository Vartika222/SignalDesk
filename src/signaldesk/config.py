"""Settings read from the environment (and .env at the repo root)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    database_url: str
    data_dir: Path

    @classmethod
    def load(cls) -> Settings:
        data_dir = Path(os.environ.get("DATA_DIR", "data"))
        if not data_dir.is_absolute():
            data_dir = REPO_ROOT / data_dir
        return cls(
            database_url=os.environ.get(
                "DATABASE_URL", "postgresql://signaldesk:signaldesk@localhost:5433/signaldesk"
            ),
            data_dir=data_dir,
        )


settings = Settings.load()
