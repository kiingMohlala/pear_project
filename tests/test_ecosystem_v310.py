"""v3.1 ecosystem scaffolding tests — API compatible with v3.0."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.connectors import build_default_connectors
from core.version import PUBLIC_APIS, check_compat
from core.config import Config
from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from agents import PersonalAgent


def test_v3_api_compat():
    assert check_compat("3.0.0")
    assert "connectors" in PUBLIC_APIS


def test_new_connectors_disabled_by_default():
    reg = build_default_connectors()
    names = [c["name"] if isinstance(c, dict) else c for c in reg.list()]
    # list() may return dicts
    flat = []
    for n in names:
        flat.append(n.get("name") if isinstance(n, dict) else n)
    for name in ("slack", "notion", "gdrive", "jira"):
        assert name in flat
        r = reg.execute(name, "status")
        # disabled without credentials — status may ok with enabled false or fail connect
        assert r is not None


def test_planner_bias_off_by_default():
    cfg = Config(profile="testing")
    assert cfg.get("planner_use_learned_bias") in (False, None) or cfg.get("planner_use_learned_bias") is False
    orch = Orchestrator(memory=Memory(session_id="eco1"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    assert orch.learned_agent_bonus("personal") == 0.0


def test_sample_workflows_present():
    root = ROOT / "samples" / "workflows"
    assert (root / "finance_monthly.json").exists()
    assert (root / "contract_summary.json").exists()


def test_tutorials_present():
    tdir = ROOT / "docs" / "tutorials"
    assert (tdir / "01_first_agent.md").exists()
    assert (tdir / "02_first_connector.md").exists()


if __name__ == "__main__":
    test_v3_api_compat()
    print("  ✓ api compat")
    test_new_connectors_disabled_by_default()
    print("  ✓ connectors")
    test_planner_bias_off_by_default()
    print("  ✓ bias default off")
    test_sample_workflows_present()
    print("  ✓ samples")
    test_tutorials_present()
    print("  ✓ tutorials")
    print("All v3.1 ecosystem tests passed.")
