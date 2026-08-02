"""Finance Agent regression tests (v0.40)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.finance import (
    parse_csv_transactions,
    parse_statement_file,
    categorize,
    monthly_summary,
    spending_by_category,
    detect_recurring,
    detect_anomalies,
    recommendations,
)
from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from agents import FinanceAgent, PersonalAgent

SAMPLE = ROOT / "evaluation/sample_bank_statements/sample_checking.csv"
SAMPLE_DC = ROOT / "evaluation/sample_bank_statements/sample_debit_credit.csv"


def test_parse_csv_basic():
    text = SAMPLE.read_text()
    txns = parse_csv_transactions(text)
    assert len(txns) >= 20
    assert any(t.amount > 0 for t in txns)
    assert any(t.amount < 0 for t in txns)


def test_parse_debit_credit():
    txns = parse_csv_transactions(SAMPLE_DC.read_text())
    assert len(txns) == 3
    assert any(t.amount < 0 for t in txns)
    assert any(t.amount > 0 for t in txns)


def test_malformed_csv():
    try:
        parse_csv_transactions("not,a,real\nheader\n")
        # may raise or return empty — both ok if no crash with good headers missing
    except ValueError:
        pass
    try:
        parse_csv_transactions("foo,bar\n1,2\n")
        assert False, "expected ValueError for unknown headers"
    except ValueError:
        pass


def test_categorization():
    assert categorize("NETFLIX.COM", -15.99) == "subscription"
    assert categorize("ACME CORP PAYROLL", 3000) == "salary"
    assert categorize("WHOLE FOODS MARKET", -50) == "groceries"
    assert categorize("RANDOM XYZ", -10) == "uncategorized"


def test_summaries_and_anomalies():
    txns = parse_statement_file(SAMPLE)
    months = monthly_summary(txns)
    assert "2026-01" in months and "2026-02" in months
    cats = spending_by_category(txns)
    assert cats.get("rent", 0) > 0
    rec = detect_recurring(txns)
    assert any("NETFLIX" in r["description"].upper() for r in rec) or len(rec) >= 1
    flags = detect_anomalies(txns)
    types = {f["type"] for f in flags}
    assert "duplicate" in types or "large_expense" in types or "subscription" in types
    tips = recommendations(txns)
    assert tips


def test_agent_import_and_reports():
    mem = Memory(session_id="fin1")
    agent = FinanceAgent(llm=EchoLLM())
    agent.memory = mem
    r = agent.think(f"import statement {SAMPLE}")
    assert r["ok"] and r.get("action") == "import_complete"
    assert r.get("added", 0) >= 20

    s = agent.think("monthly summary")
    assert s["ok"] and "2026-01" in s["reply"]

    c = agent.think("spending by category")
    assert c["ok"] and "rent" in c["reply"].lower()

    a = agent.think("show unusual spending and duplicates")
    assert a["ok"]

    q = agent.think("How much did I spend on netflix?")
    assert q["ok"]


def test_orchestrator_routes_finance():
    orch = Orchestrator(memory=Memory(session_id="fin2"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    orch.register(FinanceAgent(llm=EchoLLM()))
    task = orch.plan("analyse my budget and cashflow")
    assert task.assigned_agent == "finance"
    # import via route
    r = orch.route(f"import statement {SAMPLE}")
    assert r.get("ok") is True


def test_ledger_retrieval_index():
    mem = Memory(session_id="fin3")
    agent = FinanceAgent(llm=EchoLLM())
    agent.memory = mem
    agent.think(f"import statement {SAMPLE}")
    hits = mem.knowledge.search("netflix subscription", limit=3)
    assert hits, "ledger should be searchable"


if __name__ == "__main__":
    test_parse_csv_basic()
    print("  ✓ parse csv")
    test_parse_debit_credit()
    print("  ✓ debit/credit")
    test_malformed_csv()
    print("  ✓ malformed")
    test_categorization()
    print("  ✓ categorize")
    test_summaries_and_anomalies()
    print("  ✓ analytics")
    test_agent_import_and_reports()
    print("  ✓ agent reports")
    test_orchestrator_routes_finance()
    print("  ✓ orchestrator")
    test_ledger_retrieval_index()
    print("  ✓ retrieval index")
    print("All v0.40 finance tests passed.")
