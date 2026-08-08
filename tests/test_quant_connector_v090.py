"""PEAR Quant Connector tests (v0.9)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.connectors import build_default_connectors, QuantConnector
from core.connectors.quant_connector import FORBIDDEN_ACTIONS
from core.connectors.base import ConnectorStatus
from core.workflow import WorkflowStep, StepType


def test_discovered_in_registry():
    reg = build_default_connectors()
    assert "quant" in reg._connectors or reg.get("quant")
    q = reg.get("quant")
    assert isinstance(q, QuantConnector)
    assert "quant_research" in q.capability_names()


def test_connect_research_only():
    with tempfile.TemporaryDirectory() as td:
        q = QuantConnector(data_dir=Path(td))
        r = q.connect()
        assert r.ok
        assert q.status == ConnectorStatus.CONNECTED
        assert q.metadata.get("zero_real_orders") is True
        assert q.metadata.get("allows_capital_allocation") is False


def test_rejects_trading_credentials():
    q = QuantConnector()
    r = q.connect({"broker_token": "x", "trading_key": "y"})
    assert not r.ok


def test_forbidden_actions():
    with tempfile.TemporaryDirectory() as td:
        q = QuantConnector(data_dir=Path(td))
        q.connect()
        for action in ("place_order", "buy", "allocate", "live_trade"):
            r = q.execute(action)
            assert not r.ok
            assert "forbidden" in (r.error or "").lower()


def test_research_and_status():
    with tempfile.TemporaryDirectory() as td:
        q = QuantConnector(data_dir=Path(td))
        q.connect()
        r = q.execute("quant_research", name="sma_cross", fast=5, slow=20, symbol="BTCUSDT", n_bars=120, seed=1)
        assert r.ok
        assert r.data.get("experiment_id")
        st = q.execute("quant_status")
        assert st.ok and st.data["zero_real_orders"] is True
        c = q.execute("quant_candidates")
        assert c.ok and len(c.data["candidates"]) >= 1


def test_hypotheses_report_lineage():
    with tempfile.TemporaryDirectory() as td:
        q = QuantConnector(data_dir=Path(td))
        q.connect()
        # seed research
        for f, s in [(5, 20), (8, 30), (4, 15)]:
            q.execute("quant_research", fast=f, slow=s, n_bars=100, seed=f)
        h = q.execute("quant_hypotheses", generate=True, family="sma")
        assert h.ok
        fp = q.execute("quant_failure_patterns")
        assert fp.ok
        ms = q.execute("quant_market_summary", market="SYN")
        assert ms.ok


def test_review_decision():
    with tempfile.TemporaryDirectory() as td:
        q = QuantConnector(data_dir=Path(td))
        q.connect()
        r = q.execute("quant_review", fast=5, slow=18, evidence_count=4, markets_tested=2)
        assert r.ok
        assert "decision" in r.data


def test_registry_execute():
    with tempfile.TemporaryDirectory() as td:
        # isolate by using connector instance path via connect after build
        reg = build_default_connectors()
        # replace quant with temp data dir instance
        reg.register(QuantConnector(data_dir=Path(td)))
        r = reg.execute("quant", "quant_status")
        assert r.ok


def test_workflow_step_shape():
    step = WorkflowStep(
        name="quant_research_step",
        type=StepType.CONNECTOR,
        connector="quant",
        connector_action="quant_research",
        connector_params={"name": "sma_cross", "symbol": "BTCUSDT"},
    )
    assert step.connector == "quant"
    assert step.connector_action == "quant_research"


def test_shadow_status_no_broker():
    with tempfile.TemporaryDirectory() as td:
        q = QuantConnector(data_dir=Path(td))
        q.connect()
        r = q.execute("quant_shadow_status")
        assert r.ok
        assert r.data["allows_real_orders"] is False
        assert r.data["broker"] is None


if __name__ == "__main__":
    test_discovered_in_registry()
    print("  ✓ registry")
    test_connect_research_only()
    print("  ✓ connect")
    test_rejects_trading_credentials()
    print("  ✓ no trading creds")
    test_forbidden_actions()
    print("  ✓ forbidden")
    test_research_and_status()
    print("  ✓ research")
    test_hypotheses_report_lineage()
    print("  ✓ hypotheses")
    test_review_decision()
    print("  ✓ review")
    test_registry_execute()
    print("  ✓ reg execute")
    test_workflow_step_shape()
    print("  ✓ workflow step")
    test_shadow_status_no_broker()
    print("  ✓ shadow status")
    print("All quant connector v0.9 tests passed.")
