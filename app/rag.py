"""Vectorless RAG: retrieval with BM25 keyword scoring, no embeddings.

RAG = Retrieval-Augmented Generation:
  1. Split documents into chunks      (chunk_text)
  2. Find chunks relevant to question (bm25_search)
  3. Give those chunks to the LLM      (build_prompt)
  4. LLM answers ONLY from that context -> fewer hallucinations, answers cite sources

Why vectorless/BM25 here?
  - No embedding model, no vector DB, no extra API cost.
  - Works great when questions share words with the documents.
  - Weakness: misses synonyms ("car" vs "automobile"). Vector RAG fixes that.
"""
import math
import re
from collections import Counter

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "to", "of", "and", "or",
    "in", "on", "for", "with", "as", "by", "at", "it", "this", "that", "what",
    "how", "why", "who", "when", "which", "do", "does", "did", "from", "i", "you",
}


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if w not in STOPWORDS]


def chunk_text(text: str, max_words: int = 120, overlap: int = 20) -> list[str]:
    """Split text into overlapping word windows.

    Overlap keeps a sentence that falls on a boundary visible in both chunks.
    """
    if overlap >= max_words:
        raise ValueError("overlap must be smaller than max_words")
    words = text.split()
    if not words:
        return []
    chunks, start = [], 0
    step = max_words - overlap
    while start < len(words):
        chunks.append(" ".join(words[start:start + max_words]))
        if start + max_words >= len(words):
            break
        start += step
    return chunks


def bm25_search(query: str, docs: list[tuple[int, str]], top_k: int = 3,
                k1: float = 1.5, b: float = 0.75) -> list[tuple[int, float]]:
    """Rank (id, text) pairs against the query. Returns [(id, score)] best first.

    BM25 in one line: a chunk scores higher when it contains the query's
    RARE words (idf), several times (tf, with diminishing returns via k1),
    adjusted so long chunks don't win just by being long (b).
    """
    q_terms = tokenize(query)
    if not q_terms or not docs:
        return []

    tokenized = [(doc_id, tokenize(text)) for doc_id, text in docs]
    n = len(tokenized)
    avgdl = sum(len(t) for _, t in tokenized) / n or 1.0

    df = Counter()
    for _, toks in tokenized:
        df.update(set(toks))

    scores = []
    for doc_id, toks in tokenized:
        tf = Counter(toks)
        dl = len(toks)
        score = 0.0
        for term in q_terms:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            freq = tf[term]
            score += idf * (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * dl / avgdl))
        if score > 0:
            scores.append((doc_id, round(score, 4)))

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_k]


RAG_SYSTEM = (
    "You answer questions using ONLY the provided context. "
    "If the context does not contain the answer, say \"I don't know based on the documents.\" "
    "Cite chunk ids like [chunk 3]. Be concise."
)


def build_prompt(question: str, contexts: list[tuple[int, str]]) -> str:
    ctx = "\n\n".join(f"[chunk {cid}] {text}" for cid, text in contexts)
    return f"QUESTION: {question}\n\nCONTEXT:\n{ctx}"
