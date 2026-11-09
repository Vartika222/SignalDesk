import pytest

from signaldesk.chunk import CHUNKERS, ChunkerConfig, chunk_text, count_tokens

pytestmark = pytest.mark.models  # downloads the bge tokenizer once

PARA = "Revenue increased due to higher demand in the Americas. " * 6
TEXT = "\n\n".join(f"Paragraph {i}. {PARA.strip()}" for i in range(30))


@pytest.mark.parametrize("name", list(CHUNKERS))
def test_chunks_are_exact_substrings_within_budget(name):
    cfg = CHUNKERS[name]
    chunks = chunk_text(TEXT, cfg)
    assert len(chunks) > 1
    for c in chunks:
        assert TEXT[c.char_start : c.char_end] == c.text
        assert c.token_count <= cfg.max_tokens + 4
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(TEXT)


def test_boundary_aware_chunks_start_at_paragraphs():
    for c in chunk_text(TEXT, CHUNKERS["sec256"]):
        assert c.text.startswith("Paragraph ")


def test_overlap_repeats_trailing_paragraph():
    chunks = chunk_text(TEXT, CHUNKERS["sec512"])
    assert chunks[1].char_start < chunks[0].char_end


def test_giant_paragraph_falls_back_to_tokens():
    blob = "word " * 2000
    chunks = chunk_text(blob, ChunkerConfig("t", max_tokens=100, overlap_tokens=0))
    assert all(c.token_count <= 104 for c in chunks)
    assert sum(count_tokens([c.text])[0] for c in chunks) >= 1990


def test_empty():
    assert chunk_text("   ", CHUNKERS["sec256"]) == []
