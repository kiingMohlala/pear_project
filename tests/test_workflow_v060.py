"""Workflow engine regression tests (v0.60)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from core.workflow import (
    Workflow,
    WorkflowStep,
    WorkflowRunner,
    StepType,
    WorkflowStatus,
    StepStatus,
    render_template,
    eval_when,
    builtin_workflows,
)
from agents import PersonalAgent, FinanceAgent, LegalAgent

NDA = ROOT / "evaluation/sample_contracts/sample_nda.txt"
CSV = ROOT / "evaluation/sample_bank_statements/sample_checking.csv"


def make_orch(td: Path):
    mem = Memory(session_id="wf", persist_dir=td)
    orch = Orchestrator(memory=mem, llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    orch.register(FinanceAgent(llm=EchoLLM()))
    orch.register(LegalAgent(llm=EchoLLM()))
    return orch


def test_templates_and_when():
    ctx = {"path": "/tmp/a.csv", "ok": True, "n": 0}
    assert render_template("import {{path}}", ctx) == "import /tmp/a.csv"
    assert eval_when("{{path}}", ctx) is True
    assert eval_when("{{missing}}", {"x": 1}) is False
    assert eval_when("{{n}} == 0", ctx) is True


def test_sequential_builtin_contract():
    with tempfile.TemporaryDirectory() as td:
        orch = make_orch(Path(td))
        run = orch.workflows.start(
            "contract_review_summary",
            context={"contract_path": str(NDA)},
        )
        assert run.status == WorkflowStatus.COMPLETED, (run.status, run.error)
        assert run.current_index >= 3
        assert any(s.status == StepStatus.COMPLETED for s in run.steps)


def test_conditional_skip():
    with tempfile.TemporaryDirectory() as td:
        orch = make_orch(Path(td))
        wf = Workflow(
            name="cond_demo",
            steps=[
                WorkflowStep(name="skip_me", type=StepType.TASK, objective="hello", when="{{flag}}"),
                WorkflowStep(name="always", type=StepType.TASK, objective="hello there", save_as="hi"),
            ],
        )
        orch.workflows.register(wf)
        run = orch.workflows.start("cond_demo", context={})  # flag missing → skip
        assert run.steps[0].status == StepStatus.SKIPPED
        assert run.steps[1].status == StepStatus.COMPLETED
        assert run.status == WorkflowStatus.COMPLETED


def test_approval_pause_and_resume():
    with tempfile.TemporaryDirectory() as td:
        orch = make_orch(Path(td))
        run = orch.workflows.start(
            "import_analyze_report",
            context={
                "import_objective": f"import contract {NDA}",
                "analyze_objective": "analyze risks",
                "report_objective": "executive summary",
            },
        )
        assert run.status == WorkflowStatus.WAITING_APPROVAL
        # resume with approve
        run2 = orch.workflows.resume(run.id, approve=True)
        assert run2.status == WorkflowStatus.COMPLETED


def test_cancel():
    with tempfile.TemporaryDirectory() as td:
        orch = make_orch(Path(td))
        wf = Workflow(
            name="longish",
            steps=[
                WorkflowStep(name="a", type=StepType.APPROVAL, message="stop?"),
                WorkflowStep(name="b", type=StepType.TASK, objective="hello"),
            ],
        )
        orch.workflows.register(wf)
        run = orch.workflows.start("longish")
        assert run.status == WorkflowStatus.WAITING_APPROVAL
        c = orch.workflows.cancel(run.id)
        assert c.status == WorkflowStatus.CANCELLED


def test_persistence_recovery():
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        orch = make_orch(td_path)
        run = orch.workflows.start(
            "import_analyze_report",
            context={
                "import_objective": f"import contract {NDA}",
                "analyze_objective": "extract clauses",
                "report_objective": "executive summary",
            },
        )
        assert run.status == WorkflowStatus.WAITING_APPROVAL
        run_id = run.id

        # New orchestrator loads runs
        orch2 = make_orch(td_path)
        loaded = orch2.workflows.status(run_id)
        assert loaded["status"] in ("waiting_approval", "paused")
        resumed = orch2.workflows.resume(run_id, approve=True)
        assert resumed.status == WorkflowStatus.COMPLETED


def test_retry_on_failure():
    with tempfile.TemporaryDirectory() as td:
        orch = make_orch(Path(td))
        attempts = {"n": 0}
        original_route = orch.route

        def flaky(obj, **kwargs):
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise RuntimeError("transient")
            return original_route(obj, **kwargs)

        orch.route = flaky  # type: ignore
        wf = Workflow(
            name="retry_demo",
            steps=[
                WorkflowStep(
                    name="t",
                    type=StepType.TASK,
                    objective="hello",
                    max_retries=2,
                    on_failure="fail",
                ),
            ],
        )
        orch.workflows.register(wf)
        run = orch.workflows.start("retry_demo")
        assert run.status == WorkflowStatus.COMPLETED
        assert attempts["n"] >= 2


def test_finance_workflow():
    with tempfile.TemporaryDirectory() as td:
        orch = make_orch(Path(td))
        run = orch.workflows.start(
            "finance_monthly_review",
            context={"statement_path": str(CSV)},
        )
        assert run.status == WorkflowStatus.COMPLETED, run.error


def test_builtins_present():
    names = set(builtin_workflows())
    assert "finance_monthly_review" in names
    assert "contract_review_summary" in names
    assert "import_analyze_report" in names


if __name__ == "__main__":
    test_templates_and_when()
    print("  ✓ templates")
    test_sequential_builtin_contract()
    print("  ✓ contract workflow")
    test_conditional_skip()
    print("  ✓ conditional")
    test_approval_pause_and_resume()
    print("  ✓ approval")
    test_cancel()
    print("  ✓ cancel")
    test_persistence_recovery()
    print("  ✓ persistence")
    test_retry_on_failure()
    print("  ✓ retry")
    test_finance_workflow()
    print("  ✓ finance workflow")
    test_builtins_present()
    print("  ✓ builtins")
    print("All v0.60 workflow tests passed.")
