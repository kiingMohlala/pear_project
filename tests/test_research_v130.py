"""Research agent regression tests (v1.30)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.research_agent import (
    ResearchAgent,
    Source,
    rank_sources,
    dedupe_sources,
    domain_credibility,
)
from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from agents import PersonalAgent
from evaluation.engine import EvaluationEngine


def test_domain_credibility():
    assert domain_credibility("https://www.nih.gov/path") > domain_credibility("https://example.com/x")
    assert domain_credibility("https://arxiv.org/abs/1") >= 0.8


def test_dedupe_and_rank():
    sources = [
        Source("a", "https://example.com/1", "A", "alpha beta", 0.3, 0.0),
        Source("b", "https://example.com/1", "A", "alpha beta", 0.3, 0.0),
        Source("c", "https://en.wikipedia.org/wiki/X", "X", "alpha research methods", 0.75, 0.0),
    ]
    d = dedupe_sources(sources)
    assert len(d) == 2
    ranked = rank_sources(d, "alpha research")
    assert ranked[0].rank_score >= ranked[-1].rank_score


def test_agent_research_report():
    agent = ResearchAgent(llm=EchoLLM())
    agent.memory = Memory(session_id="r1")
    r = agent.think("research renewable energy storage")
    assert r["ok"]
    assert r.get("sources")
    assert "Research report" in r["reply"]
    assert "[1]" in r["reply"]
    # sources command
    s = agent.think("sources")
    assert s["ok"] and "http" in s["reply"]
    rep = agent.think("research report")
    assert rep["ok"] and len(rep["reply"]) > 50


def test_planner_routes_research():
    orch = Orchestrator(memory=Memory(session_id="r2"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    orch.register(ResearchAgent(llm=EchoLLM()))
    task = orch.plan("research the history of the internet with sources")
    assert task.assigned_agent == "research"


def test_eval_suite_research():
    eng = EvaluationEngine()
    report = eng.run(suites=["research"], save_history=False, compare_baseline=False)
    assert report.suites["research"].success_rate >= 0.75


if __name__ == "__main__":
    test_domain_credibility()
    print("  ✓ credibility")
    test_dedupe_and_rank()
    print("  ✓ rank/dedupe")
    test_agent_research_report()
    print("  ✓ agent report")
    test_planner_routes_research()
    print("  ✓ planner")
    test_eval_suite_research()
    print("  ✓ eval suite")
    print("All v1.30 research tests passed.")
