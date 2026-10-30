"""Unit tests for the pure functions (no API, no DB)."""
import pytest

from app.rag import bm25_search, build_prompt, chunk_text, tokenize


def test_tokenize_lowercases_and_drops_stopwords():
    assert tokenize("What is the Refund Policy?") == ["refund", "policy"]


def test_chunk_text_overlaps():
    text = " ".join(f"w{i}" for i in range(250))
    chunks = chunk_text(text, max_words=100, overlap=20)
    assert len(chunks) == 3
    assert chunks[0].split()[-20:] == chunks[1].split()[:20]  # overlap preserved


def test_chunk_text_empty_and_bad_args():
    assert chunk_text("   ") == []
    with pytest.raises(ValueError):
        chunk_text("a b c", max_words=5, overlap=5)


def test_bm25_ranks_relevant_first():
    docs = [
        (1, "Our office is in Hyderabad and opens at 9 am."),
        (2, "Refunds are processed within 7 days of the refund request."),
        (3, "The cafeteria serves lunch daily."),
    ]
    results = bm25_search("how long does a refund take", docs, top_k=2)
    assert results[0][0] == 2
    assert all(score > 0 for _, score in results)


def test_bm25_no_match_returns_empty():
    assert bm25_search("quantum", [(1, "apples and oranges")]) == []
    assert bm25_search("", [(1, "text")]) == []


def test_build_prompt_includes_chunk_ids():
    p = build_prompt("Q?", [(5, "alpha"), (9, "beta")])
    assert "[chunk 5] alpha" in p and "[chunk 9] beta" in p
