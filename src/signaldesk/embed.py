"""Local embedding models (run on Apple Silicon via MPS when available)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np


@dataclass(frozen=True)
class EmbeddingModel:
    key: str  # short name stored in embeddings.model
    hf_name: str
    dim: int
    query_prefix: str = ""


BGE_QUERY = "Represent this sentence for searching relevant passages: "

MODELS: dict[str, EmbeddingModel] = {
    "bge-small": EmbeddingModel("bge-small", "BAAI/bge-small-en-v1.5", 384, BGE_QUERY),
    "bge-base": EmbeddingModel("bge-base", "BAAI/bge-base-en-v1.5", 768, BGE_QUERY),
}


def passage_input(company: str, form: str, section_title: str, text: str) -> str:
    """Contextual header: the embedding sees which company, form and section a passage is from."""
    return f"{company} | {form} | {section_title}\n{text}"


def device() -> str:
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


@lru_cache(maxsize=4)
def _load(hf_name: str):
    from sentence_transformers import SentenceTransformer

    # Cached weights first: no network round trip, and runs don't depend on the Hub being reachable.
    try:
        m = SentenceTransformer(hf_name, device=device(), local_files_only=True)
    except OSError:
        m = SentenceTransformer(hf_name, device=device())
    m.max_seq_length = 512
    return m


def embed_passages(model: EmbeddingModel, texts: list[str], batch_size: int = 32) -> np.ndarray:
    return _load(model.hf_name).encode(
        texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False,
        convert_to_numpy=True,
    )


def embed_query(model: EmbeddingModel, text: str) -> np.ndarray:
    return _load(model.hf_name).encode(
        [model.query_prefix + text], normalize_embeddings=True, convert_to_numpy=True
    )[0]
