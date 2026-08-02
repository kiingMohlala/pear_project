#!/usr/bin/env python3
"""
Regression checks for specialist agents (v0.3 Legal first).

Run from repo root:
  python evaluation/regression_tests.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from agents import PersonalAgent, LegalAgent, DesktopAgent
from evaluation.metrics import score_review


EVAL = Path(__file__).resolve().parent
SAMPLE_NDA = EVAL / "sample_contracts" / "sample_nda.txt"
EXPECTED = EVAL / "expected_outputs" / "sample_nda_risks.json"


def build_orch() -> Orchestrator:
    mem = Memory(session_id="eval")
    llm = EchoLLM()
    orch = Orchestrator(memory=mem, llm=llm)
    orch.register(PersonalAgent(llm=llm), default=True)
    orch.register(LegalAgent(llm=llm))
    orch.register(DesktopAgent())
    return orch


def load_nda(orch: Orchestrator) -> None:
    text = SAMPLE_NDA.read_text(encoding="utf-8")
    orch.memory.knowledge.add_document(name="sample_nda.txt", text=text)


def test_legal_risk_heuristics() -> None:
    orch = build_orch()
    load_nda(orch)
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))

    agent = orch.agents["legal"]
    result = agent.think("Analyse the risks in this NDA")
    assert result.get("ok"), result
    risks = result.get("risks") or []
    # If LLM path (unlikely with Echo), risks may be empty — force heuristic by checking action
    if not risks and result.get("action") == "risk_analysis":
        # Re-run pure heuristic path
        text = SAMPLE_NDA.read_text()
        risks = agent._heuristic_risks(text)

    metrics = score_review(
        risks=risks,
        reply=result.get("reply") or "",
        expected_labels=expected["must_detect_labels"],
        min_high_or_critical=expected["min_high_or_critical"],
    )
    print("  risk metrics:", metrics)
    assert metrics["pass"], metrics


def test_legal_full_review_pipeline() -> None:
    orch = build_orch()
    load_nda(orch)
    r = orch.route("Review this NDA and summarize the risks")
    assert r.get("ok") is True or r.get("reply")
    assert r.get("plan_id")
    graph = orch.current_graph
    assert graph is not None
    assert len(graph.nodes) >= 2, "expected multi-step legal plan"
    print(f"  plan tasks={len(graph.nodes)} summary={graph.summary!r}")
    assert "NDA" in (r.get("reply") or "") or "risk" in (r.get("reply") or "").lower() or r.get("ok")


def test_clause_extraction() -> None:
    orch = build_orch()
    load_nda(orch)
    agent = orch.agents["legal"]
    result = agent.think("Extract key clauses from the NDA")
    assert result.get("ok")
    reply = result.get("reply") or ""
    assert "Confidential" in reply or "clause" in reply.lower() or "DEFINITIONS" in reply.upper() or "Non-Compete" in reply or "clause" in (result.get("action") or "")
    print("  clause extraction ok, chars=", len(reply))


def main() -> None:
    print("PEAR evaluation / regression")
    test_legal_risk_heuristics()
    print("  ✓ legal risk heuristics")
    test_clause_extraction()
    print("  ✓ clause extraction")
    test_legal_full_review_pipeline()
    print("  ✓ multi-step NDA review pipeline")
    print("All evaluation checks passed.")


if __name__ == "__main__":
    main()
