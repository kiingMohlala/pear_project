"""Memory intelligence regression tests (v1.80)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.memory import Memory
from core.memory_intelligence import MemoryIntelligence, jaccard, tokenize
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from evaluation.engine import EvaluationEngine


def test_score_and_preference():
    mi = MemoryIntelligence(Memory(session_id="mi1"))
    item = mi.observe("I prefer concise answers")
    assert item.category == "preference"
    assert 0 <= item.importance <= 1


def test_dedupe_frequency():
    mi = MemoryIntelligence(Memory(session_id="mi2"))
    a = mi.observe("Ship the release on Monday")
    b = mi.observe("Ship the release on Monday")
    assert a.id == b.id
    assert b.frequency >= 2


def test_consolidate_and_cluster():
    mi = MemoryIntelligence(Memory(session_id="mi3"), consolidate_threshold=0.3)
    mi.observe("Research notes on solar batteries efficiency")
    mi.observe("Research notes on solar batteries cost")
    mi.observe("Research notes on solar batteries lifespan")
    clusters = mi.cluster()
    assert clusters
    summaries = mi.consolidate()
    assert summaries or any(i.category == "summary" for i in mi.items.values())


def test_archive_decay():
    import time
    mi = MemoryIntelligence(Memory(session_id="mi4"), archive_threshold=0.2)
    item = mi.observe("temporary scratch pad alpha")
    # age the memory so decay drives importance below threshold
    item.created_at = time.time() - 86400 * 400
    item.last_access = item.created_at
    item.frequency = 1
    item.category = "general"
    n = mi.archive_low_value()
    assert n >= 1
    assert item.category == "archive"


def test_search():
    mi = MemoryIntelligence(Memory(session_id="mi5"))
    mi.observe("OCR pipeline for scanned invoices")
    hits = mi.search("invoice OCR")
    assert hits


def test_cleanup_stats():
    mi = MemoryIntelligence(Memory(session_id="mi6"))
    mi.observe("I like keyboard shortcuts")
    mi.observe("random low signal")
    out = mi.cleanup()
    assert "active" in out
    stats = mi.memory_stats()
    assert stats["total"] >= 1


def test_orchestrator_wired():
    orch = Orchestrator(memory=Memory(session_id="mi7"), llm=EchoLLM())
    assert hasattr(orch, "memory_intel")
    orch.memory_intel.observe("My name is Ada")
    assert orch.memory.long_term.get_pref("name") == "Ada" or "Ada" in str(orch.memory_intel.memory_stats())


def test_eval_suite():
    eng = EvaluationEngine()
    report = eng.run(suites=["memory_intel"], save_history=False, compare_baseline=False)
    assert report.suites["memory_intel"].success_rate >= 0.8


if __name__ == "__main__":
    test_score_and_preference()
    print("  ✓ score/pref")
    test_dedupe_frequency()
    print("  ✓ dedupe")
    test_consolidate_and_cluster()
    print("  ✓ consolidate")
    test_archive_decay()
    print("  ✓ archive")
    test_search()
    print("  ✓ search")
    test_cleanup_stats()
    print("  ✓ cleanup")
    test_orchestrator_wired()
    print("  ✓ orchestrator")
    test_eval_suite()
    print("  ✓ eval")
    print("All v1.80 memory intelligence tests passed.")
