#!/usr/bin/env python3
"""
Retrieval evaluation for v0.32 semantic search.

Measures Top-1 / Top-3 accuracy on a small labeled question set
over sample contracts (works with NullEmbeddings — no model required).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.memory import KnowledgeStore, chunk_text
from core.embeddings import NullEmbeddings, create_embeddings
from core.vector_store import VectorStore

EVAL = Path(__file__).resolve().parent
SAMPLE = EVAL / "sample_contracts" / "sample_nda.txt"

# (question, must_appear_in_top_k_snippet_or_title) — keywords that should rank high
QUESTIONS = [
    ("What are the non-compete obligations?", ["non-compete", "compete"]),
    ("Who owns intellectual property?", ["intellectual property", "work for hire", "owned"]),
    ("Is there unlimited liability or indemnification?", ["indemnif", "unlimited liability"]),
    ("How long does confidentiality survive?", ["perpetual", "survive", "confidential"]),
    ("What is the governing law and arbitration venue?", ["governing law", "arbitration", "johannesburg"]),
]


def build_store() -> KnowledgeStore:
    ks = KnowledgeStore(embeddings=NullEmbeddings(), vector_store=VectorStore())
    text = SAMPLE.read_text(encoding="utf-8")
    ks.add_document(name="sample_nda.txt", text=text, source_path=str(SAMPLE))
    return ks


def topk_hit(hits, needles, k: int) -> bool:
    for h in hits[:k]:
        blob = f"{h.get('title','')} {h.get('snippet','')}".lower()
        if any(n.lower() in blob for n in needles):
            return True
    return False


def test_chunking():
    words = " ".join(f"w{i}" for i in range(1200))
    chunks = chunk_text(words, chunk_size=500, overlap=80)
    assert len(chunks) >= 2
    # overlap means consecutive chunks share content
    assert len(chunks[0].split()) == 500


def test_vector_store_crud():
    vs = VectorStore()
    rid = vs.add(text="hello world", vector=[1.0, 0.0, 0.0], metadata={"doc_id": "d1"})
    assert len(vs) == 1
    hits = vs.search([1.0, 0.0, 0.0], top_k=1)
    assert hits and hits[0][0].id == rid
    vs.delete(rid)
    assert len(vs) == 0


def test_retrieval_accuracy():
    ks = build_store()
    top1 = top3 = 0
    for q, needles in QUESTIONS:
        hits = ks.search(q, limit=5)
        if topk_hit(hits, needles, 1):
            top1 += 1
            top3 += 1
        elif topk_hit(hits, needles, 3):
            top3 += 1
        print(f"  Q: {q[:50]}…  top1={topk_hit(hits, needles, 1)} top3={topk_hit(hits, needles, 3)}")
    n = len(QUESTIONS)
    print(f"  Top-1 accuracy: {top1}/{n} = {top1/n:.2f}")
    print(f"  Top-3 accuracy: {top3}/{n} = {top3/n:.2f}")
    # Null embeddings + hybrid keyword should still clear a modest bar
    assert top3 / n >= 0.6, "Top-3 retrieval too weak"
    assert top1 / n >= 0.4, "Top-1 retrieval too weak"


def test_build_context_unchanged_api():
    ks = build_store()
    ctx = ks.build_context("indemnification liability")
    assert isinstance(ctx, str)
    assert len(ctx) > 20


def test_agents_unchanged_import():
    """Agents still import Memory / KnowledgeStore without new args."""
    from agents import PersonalAgent, LegalAgent
    from core.memory import Memory
    from core.llm import EchoLLM

    m = Memory(session_id="ret")
    text = SAMPLE.read_text(encoding="utf-8")
    m.knowledge.add_document(name="sample_nda.txt", text=text)
    agent = LegalAgent(llm=EchoLLM())
    agent.memory = m
    r = agent.think("Analyse the risks in this NDA")
    assert r.get("ok") is True


if __name__ == "__main__":
    print("PEAR retrieval evaluation")
    test_chunking()
    print("  ✓ chunking")
    test_vector_store_crud()
    print("  ✓ vector store CRUD")
    test_retrieval_accuracy()
    print("  ✓ retrieval accuracy")
    test_build_context_unchanged_api()
    print("  ✓ build_context API")
    test_agents_unchanged_import()
    print("  ✓ agents unchanged")
    print("All retrieval checks passed.")
