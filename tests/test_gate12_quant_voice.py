"""
PEAR 3.1 Gate 12 — Quant persistence race + Voice ownership.

Fixes two issues from the Re-Audit 3 handoff/report, neither caught by
Gates 1-11:

1. All three Quant JSON stores (research_memory.json, hypotheses.json,
   review_board.json) were constructed fresh per user session, each
   loading the whole file into a private in-memory snapshot once, then
   overwriting the whole file on every save. Concurrent sessions raced
   to clobber each other's writes wholesale. Re-Audit 3 reproduced this
   directly: 30 concurrent callers each added one research experiment,
   only 2 of 30 survived on disk.

2. VoiceAssistant defaulted its media_dir to the machine-global
   ~/PEAR_Workspace/voice regardless of which user's Orchestrator
   constructed it — same bug class as Gate 10 (browser) and Gate 11
   (workspace/calendar/media).

Each test below is written to fail against pre-fix code (verified via
`git stash` — see Gate 12 handoff notes) and pass against the fix.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quant.research_memory import ResearchMemory
from quant.experiment import ExperimentRecord, Disposition
from quant.hypothesis_engine import HypothesisEngine
from quant.research_lab import ResearchLab
from quant.research_review import ResearchReviewBoard
from quant.scorecard import CandidateScorecard
from core.orchestrator import Orchestrator
from core.memory import Memory
from core.llm import EchoLLM


def _mk_experiment(i: int) -> ExperimentRecord:
    return ExperimentRecord(
        id=f"exp-gate12-{i:03d}",
        strategy_fingerprint=f"fp-{i:03d}",
        strategy_family="sma",
        strategy_name=f"sma_cross_{i}",
        strategy_spec={"fast": 5, "slow": 20},
        market=f"USER_{i:03d}_SECRET_MARKET",
        timeframe="1h",
        dataset_id=f"ds-{i:03d}",
        parameters={"fast": 5, "slow": 20},
        backtest={"sharpe": 1.0, "trades": 10, "profit_factor": 1.2, "max_drawdown": 0.1},
        oos={"sharpe": 0.9, "trades": 8, "max_drawdown": 0.12},
        disposition=Disposition.SURVIVED,
    )


def test_research_memory_concurrent_adds_all_survive():
    """30 independently-constructed ResearchMemory instances (one per
    simulated user session, exactly how QuantConnector builds one per
    Orchestrator) each add() one experiment concurrently. Pre-fix: most
    additions vanish (last writer wins). Post-fix: all 30 survive."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "research_memory.json"
        errors = []

        def worker(i):
            try:
                mem = ResearchMemory(path=path)  # fresh instance per "user", like QuantConnector
                mem.add(_mk_experiment(i))
            except Exception as e:
                errors.append((i, repr(e)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"unexpected errors: {errors}"
        final = ResearchMemory(path=path)
        assert len(final.experiments) == 30, (
            f"expected all 30 concurrent experiments to survive, found {len(final.experiments)} "
            "— this is the Gate 12 lost-write race regressing"
        )
        for i in range(30):
            assert f"exp-gate12-{i:03d}" in final.experiments


def test_hypothesis_store_concurrent_rejections_all_survive():
    """Same race, different store: hypotheses.json via reject_ungrounded(),
    the simplest mutator that touches disk. 20 concurrent callers, each
    against a freshly-constructed HypothesisEngine sharing one memory."""
    with tempfile.TemporaryDirectory() as td:
        mem_path = Path(td) / "mem.json"
        h_path = Path(td) / "hyp.json"
        errors = []
        ids = []
        ids_lock = threading.Lock()

        def worker(i):
            try:
                eng = HypothesisEngine(memory=ResearchMemory(path=mem_path), persist_path=h_path)
                h = eng.reject_ungrounded(f"idea-{i}")
                with ids_lock:
                    ids.append(h.id)
            except Exception as e:
                errors.append((i, repr(e)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"unexpected errors: {errors}"
        final = HypothesisEngine(memory=ResearchMemory(path=mem_path), persist_path=h_path)
        assert len(final.hypotheses) == 20, (
            f"expected all 20 concurrently-rejected hypotheses to survive, found {len(final.hypotheses)}"
        )
        for hid in ids:
            assert hid in final.hypotheses


def test_review_board_concurrent_decisions_all_survive():
    """Third store: review_board.json. Concurrently record scorecards
    directly (bypassing the full validator pipeline, which needs real
    price series) and confirm none are lost, and that scorecards
    persisted by _save() actually come back on the next _load() (a
    separate, pre-existing bug fixed alongside the race: _load() only
    ever restored `decisions`, never `scorecards`)."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "review.json"
        errors = []

        def worker(i):
            try:
                board = ResearchReviewBoard(persist_path=path)
                card = CandidateScorecard(candidate_id=f"cand-{i:03d}", trade_count=10, evidence_count=3)
                card.compute_composite()
                from core.security import locked_json_store
                with locked_json_store(board.persist_path):
                    board._load()
                    board.scorecards[card.candidate_id] = card
                    board._save()
            except Exception as e:
                errors.append((i, repr(e)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(25)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"unexpected errors: {errors}"
        final = ResearchReviewBoard(persist_path=path)
        assert len(final.scorecards) == 25, (
            f"expected all 25 concurrently-added scorecards to survive reload, found {len(final.scorecards)}"
        )


def test_voice_media_dir_scoped_per_user():
    """VoiceAssistant must not default to a machine-global path when an
    Orchestrator with a real per-user persist_dir constructs it."""
    with tempfile.TemporaryDirectory() as td:
        alice_dir = Path(td) / "alice"
        bob_dir = Path(td) / "bob"
        orch_alice = Orchestrator(memory=Memory(session_id="alice", persist_dir=alice_dir), llm=EchoLLM())
        orch_bob = Orchestrator(memory=Memory(session_id="bob", persist_dir=bob_dir), llm=EchoLLM())

        assert orch_alice.voice.media_dir != orch_bob.voice.media_dir, (
            "VoiceAssistant.media_dir is shared across users — Gate 12 regression "
            "(same bug class as Gate 10/Gate 11)"
        )
        assert str(alice_dir) in str(orch_alice.voice.media_dir)
        assert str(bob_dir) in str(orch_bob.voice.media_dir)


def test_bare_construction_without_persist_dir_still_falls_back_home():
    """Deliberately preserved: bare/CLI construction with no per-user
    persist_dir at all still falls back to the shared home-relative
    default — same documented exception Gate 11 established for
    Workspace/Calendar, not a regression."""
    from core.voice import VoiceAssistant
    v = VoiceAssistant()
    assert v.media_dir == Path.home() / "PEAR_Workspace" / "voice"
