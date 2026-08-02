"""Multi-agent collaboration regression tests (v1.90)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.collaboration import (
    CollaborationManager,
    CollabMode,
    heuristic_review,
    parse_review,
    disagreement_score,
    estimate_confidence,
)
from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from agents import PersonalAgent, ResearchAgent, ReviewerAgent, CriticAgent


def test_parse_and_heuristic_review():
    r = parse_review("SCORE: 0.8\nFEEDBACK: Solid.\nISSUES: none")
    assert 0.79 <= r.score <= 0.81
    h = heuristic_review("explain photosynthesis", "Photosynthesis converts light to energy in plants using chlorophyll.")
    assert h.score > 0.4


def test_disagreement():
    assert disagreement_score(["hello world", "hello world"]) == 0.0
    assert disagreement_score(["alpha beta", "completely different text here"]) > 0.5


def test_choose_mode():
    orch = Orchestrator(memory=Memory(session_id="c1"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    cm = CollaborationManager(orch)
    assert cm.choose_mode("please reach consensus on the plan") == CollabMode.CONSENSUS
    assert cm.choose_mode("review this answer for quality") == CollabMode.REVIEWER


def test_reviewer_loop_improves_or_scores():
    orch = Orchestrator(memory=Memory(session_id="c2"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    orch.register(ReviewerAgent(llm=EchoLLM()))
    cm = CollaborationManager(orch, min_review_score=0.3, max_rounds=2)
    result = cm.run("note: collaboration test item", agents=["personal"], mode="reviewer")
    assert result.mode == "reviewer"
    assert result.rounds >= 1
    assert result.reply


def test_consensus_and_parallel():
    orch = Orchestrator(memory=Memory(session_id="c3"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    orch.register(ResearchAgent(llm=EchoLLM()))
    orch.register(ReviewerAgent(llm=EchoLLM()))
    cm = CollaborationManager(orch)
    r = cm.run("research basic facts about water", agents=["personal", "research"], mode="consensus")
    assert r.ok
    assert r.contributions
    r2 = cm.run("say hello", agents=["personal"], mode="parallel")
    assert r2.ok


def test_failure_recovery():
    orch = Orchestrator(memory=Memory(session_id="c4"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    cm = CollaborationManager(orch)
    # unknown agent skipped / handled
    r = cm.run("hello", agents=["personal", "no_such_agent"], mode="sequential")
    assert r.contributions
    # at least one contribution exists
    assert len(r.contributions) >= 1


def test_orchestrator_has_collaboration():
    orch = Orchestrator(memory=Memory(session_id="c5"), llm=EchoLLM())
    assert hasattr(orch, "collaboration")
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    orch.register(ReviewerAgent(llm=EchoLLM()))
    r = orch.collaboration.run("brief status update", mode="sequential")
    assert r.reply is not None


if __name__ == "__main__":
    test_parse_and_heuristic_review()
    print("  ✓ review parse")
    test_disagreement()
    print("  ✓ disagreement")
    test_choose_mode()
    print("  ✓ mode select")
    test_reviewer_loop_improves_or_scores()
    print("  ✓ reviewer loop")
    test_consensus_and_parallel()
    print("  ✓ consensus/parallel")
    test_failure_recovery()
    print("  ✓ failure recovery")
    test_orchestrator_has_collaboration()
    print("  ✓ orchestrator")
    print("All v1.90 collaboration tests passed.")
