"""Legal Agent production tests (v0.50)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.legal import (
    detect_document_type,
    extract_clauses,
    tag_concepts,
    compare_clause_lists,
    find_missing_clauses,
)
from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from agents import LegalAgent, PersonalAgent

NDA = ROOT / "evaluation/sample_contracts/sample_nda.txt"
NDA_V2 = ROOT / "evaluation/sample_contracts/sample_nda_v2.txt"
EMP = ROOT / "evaluation/sample_contracts/sample_employment.txt"


def test_document_type_detection():
    nda = NDA.read_text()
    assert detect_document_type(nda, "sample_nda.txt") == "nda"
    emp = EMP.read_text()
    assert detect_document_type(emp, "employment.txt") == "employment"


def test_clause_extraction():
    text = NDA.read_text()
    clauses = extract_clauses(text)
    assert len(clauses) >= 6
    titles = " ".join(c.title.upper() for c in clauses)
    assert "CONFIDENTIAL" in titles or "NON-COMPETE" in titles or "INDEMNIFICATION" in titles
    concepts = tag_concepts(text)
    assert "confidentiality" in concepts
    assert "indemnity" in concepts or "liability" in concepts


def test_compare_versions():
    a = extract_clauses(NDA.read_text())
    b = extract_clauses(NDA_V2.read_text())
    diff = compare_clause_lists(a, b)
    # v2 adds RETURN OF MATERIALS; modifies non-compete duration / liability
    assert diff["added"] or diff["modified"] or diff["removed"]


def test_malformed_empty():
    assert extract_clauses("") == []
    clauses = extract_clauses("Just a plain paragraph with no headings at all about liability.")
    assert len(clauses) == 1


def test_agent_import_and_review():
    mem = Memory(session_id="leg1")
    agent = LegalAgent(llm=EchoLLM())
    agent.memory = mem
    r = agent.think(f"import contract {NDA}")
    assert r["ok"] and r.get("action") == "import_complete"
    assert r.get("doc_type") == "nda"
    assert r.get("clause_count", 0) >= 6

    risks = agent.think("analyze risks in the NDA")
    assert risks["ok"]
    assert risks.get("risks")

    summary = agent.think("executive summary")
    assert summary["ok"] and "summary" in summary["reply"].lower() or "Type:" in summary["reply"]

    clauses = agent.think("extract clauses")
    assert clauses["ok"] and clauses.get("clauses")

    qa = agent.think("What does the non-compete clause say?")
    assert qa["ok"]


def test_compare_via_agent():
    mem = Memory(session_id="leg2")
    agent = LegalAgent(llm=EchoLLM())
    agent.memory = mem
    agent.think(f"import contract {NDA}")
    agent.think(f"import contract {NDA_V2}")
    r = agent.think("compare contracts sample_nda sample_nda_v2")
    assert r["ok"] and r.get("action") == "compare"
    assert sum(r.get("diff", {}).values()) >= 1


def test_orchestrator_legal_route():
    orch = Orchestrator(memory=Memory(session_id="leg3"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    orch.register(LegalAgent(llm=EchoLLM()))
    task = orch.plan("review this NDA and flag liability risks")
    assert task.assigned_agent == "legal"
    r = orch.route(f"import contract {NDA}")
    assert r.get("ok")


def test_clause_retrieval_index():
    mem = Memory(session_id="leg4")
    agent = LegalAgent(llm=EchoLLM())
    agent.memory = mem
    agent.think(f"import contract {NDA}")
    hits = mem.knowledge.search("unlimited liability indemnification", limit=3)
    assert hits


if __name__ == "__main__":
    test_document_type_detection()
    print("  ✓ doc type")
    test_clause_extraction()
    print("  ✓ clauses")
    test_compare_versions()
    print("  ✓ compare")
    test_malformed_empty()
    print("  ✓ malformed")
    test_agent_import_and_review()
    print("  ✓ agent review")
    test_compare_via_agent()
    print("  ✓ agent compare")
    test_orchestrator_legal_route()
    print("  ✓ orchestrator")
    test_clause_retrieval_index()
    print("  ✓ retrieval")
    print("All v0.50 legal tests passed.")
