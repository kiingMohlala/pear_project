"""
Workflow engine (v0.60) – reusable multi-step automations.

Steps run sequentially by default; support when-conditions, parallel groups,
approvals, nested workflows, and planner/job/tool invocations.
Agents are not modified — the runner calls Orchestrator.route / jobs / tools.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .orchestrator import Orchestrator


class StepType(str, Enum):
    TASK = "task"              # route objective through planner
    JOB = "job"                # submit background job and wait
    TOOL = "tool"              # call tool registry
    WORKFLOW = "workflow"      # nested workflow
    APPROVAL = "approval"      # human gate
    SET = "set"                # set context variable
    PARALLEL = "parallel"      # run children concurrently (best-effort)
    CONNECTOR = "connector"    # external connector action
    MEDIA = "media"            # speech / vision / OCR


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


@dataclass
class WorkflowStep:
    name: str
    type: StepType = StepType.TASK
    # task/job
    objective: str = ""
    # tool
    tool: str = ""
    tool_args: Dict[str, Any] = field(default_factory=dict)
    # nested
    workflow: str = ""
    connector: str = ""
    connector_action: str = ""
    connector_params: Dict[str, Any] = field(default_factory=dict)
    media_action: str = ""  # transcribe | ocr | describe | process
    media_path: str = ""
    # parallel children (list of step dicts)
    steps: List[Dict[str, Any]] = field(default_factory=list)
    # conditions / io
    when: str = ""                 # expression over context, empty = always
    save_as: str = ""              # store step result under this key
    on_failure: str = "fail"       # fail | continue | retry
    max_retries: int = 0
    # approval
    message: str = ""
    # runtime
    id: str = field(default_factory=lambda: f"step_{uuid.uuid4().hex[:8]}")
    status: StepStatus = StepStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    attempts: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value if isinstance(self.type, StepType) else self.type
        d["status"] = self.status.value if isinstance(self.status, StepStatus) else self.status
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowStep":
        d = dict(data)
        if "type" in d and not isinstance(d["type"], StepType):
            d["type"] = StepType(d["type"])
        if "status" in d and not isinstance(d["status"], StepStatus):
            d["status"] = StepStatus(d["status"])
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Workflow:
    name: str
    description: str = ""
    steps: List[WorkflowStep] = field(default_factory=list)
    version: str = "1"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "metadata": self.metadata,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Workflow":
        steps = [WorkflowStep.from_dict(s) for s in data.get("steps") or []]
        return cls(
            name=data.get("name") or "unnamed",
            description=data.get("description") or "",
            version=str(data.get("version") or "1"),
            metadata=dict(data.get("metadata") or {}),
            steps=steps,
        )


@dataclass
class WorkflowRun:
    workflow_name: str
    id: str = field(default_factory=lambda: f"wfr_{uuid.uuid4().hex[:10]}")
    user_id: Optional[str] = None  # PEAR 3.1 Gate 4: owning authenticated identity
    status: WorkflowStatus = WorkflowStatus.PENDING
    context: Dict[str, Any] = field(default_factory=dict)
    steps: List[WorkflowStep] = field(default_factory=list)
    current_index: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error: Optional[str] = None
    approval_message: Optional[str] = None
    checkpoint: Dict[str, Any] = field(default_factory=dict)

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workflow_name": self.workflow_name,
            "user_id": self.user_id,
            "status": self.status.value if isinstance(self.status, WorkflowStatus) else self.status,
            "context": self.context,
            "steps": [s.to_dict() for s in self.steps],
            "current_index": self.current_index,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "approval_message": self.approval_message,
            "checkpoint": self.checkpoint,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowRun":
        steps = [WorkflowStep.from_dict(s) for s in data.get("steps") or []]
        status = data.get("status", "pending")
        if not isinstance(status, WorkflowStatus):
            status = WorkflowStatus(status)
        return cls(
            id=data.get("id") or f"wfr_{uuid.uuid4().hex[:10]}",
            workflow_name=data.get("workflow_name") or "",
            user_id=data.get("user_id"),
            status=status,
            context=dict(data.get("context") or {}),
            steps=steps,
            current_index=int(data.get("current_index") or 0),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            completed_at=data.get("completed_at"),
            error=data.get("error"),
            approval_message=data.get("approval_message"),
            checkpoint=dict(data.get("checkpoint") or {}),
        )


# ── template helpers ──────────────────────────────────────────────

_VAR = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def render_template(value: Any, context: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        def repl(m):
            key = m.group(1)
            cur: Any = context
            for part in key.split("."):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    return m.group(0)
            return str(cur)

        return _VAR.sub(repl, value)
    if isinstance(value, dict):
        return {k: render_template(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [render_template(v, context) for v in value]
    return value


def eval_when(expr: str, context: Dict[str, Any]) -> bool:
    """Very small expression evaluator: truthy lookups and comparisons."""
    expr = (expr or "").strip()
    if not expr:
        return True
    # {{var}} style
    rendered = render_template(expr, context)
    # unresolved template → false
    if isinstance(rendered, str) and _VAR.search(rendered):
        return False
    if rendered in ("", "0", "false", "False", "None", "null"):
        return False
    # equality: a == b
    m = re.match(r"^(.+?)\s*(==|!=)\s*(.+)$", str(rendered))
    if m:
        left, op, right = m.group(1).strip().strip("'\""), m.group(2), m.group(3).strip().strip("'\"")
        if op == "==":
            return left == right
        return left != right
    return bool(rendered)


# ── built-in definitions ──────────────────────────────────────────

def builtin_workflows() -> Dict[str, Workflow]:
    return {
        "finance_monthly_review": Workflow(
            name="finance_monthly_review",
            description="Import statement (if path given) → monthly summary → categories → recommendations",
            steps=[
                WorkflowStep(
                    name="import",
                    type=StepType.TASK,
                    objective="import statement {{statement_path}}",
                    when="{{statement_path}}",
                    save_as="import_result",
                    on_failure="continue",
                ),
                WorkflowStep(
                    name="monthly_summary",
                    type=StepType.TASK,
                    objective="monthly summary",
                    save_as="summary",
                ),
                WorkflowStep(
                    name="categories",
                    type=StepType.TASK,
                    objective="spending by category",
                    save_as="categories",
                ),
                WorkflowStep(
                    name="recommendations",
                    type=StepType.TASK,
                    objective="budget recommendations",
                    save_as="tips",
                ),
            ],
        ),
        "contract_review_summary": Workflow(
            name="contract_review_summary",
            description="Import contract → extract clauses → risk analysis → executive summary",
            steps=[
                WorkflowStep(
                    name="import",
                    type=StepType.TASK,
                    objective="import contract {{contract_path}}",
                    when="{{contract_path}}",
                    save_as="import_result",
                    on_failure="continue",
                ),
                WorkflowStep(
                    name="clauses",
                    type=StepType.TASK,
                    objective="extract clauses",
                    save_as="clauses",
                ),
                WorkflowStep(
                    name="risks",
                    type=StepType.TASK,
                    objective="analyze risks in the contract",
                    save_as="risks",
                ),
                WorkflowStep(
                    name="summary",
                    type=StepType.TASK,
                    objective="executive summary of the contract",
                    save_as="summary",
                ),
            ],
        ),
        "import_analyze_report": Workflow(
            name="import_analyze_report",
            description="Generic import → analyze → report pipeline with optional approval before report",
            steps=[
                WorkflowStep(
                    name="import",
                    type=StepType.TASK,
                    objective="{{import_objective}}",
                    save_as="import_result",
                ),
                WorkflowStep(
                    name="analyze",
                    type=StepType.TASK,
                    objective="{{analyze_objective}}",
                    save_as="analysis",
                ),
                WorkflowStep(
                    name="approve_report",
                    type=StepType.APPROVAL,
                    message="Approve generation of final report?",
                ),
                WorkflowStep(
                    name="report",
                    type=StepType.TASK,
                    objective="{{report_objective}}",
                    save_as="report",
                ),
            ],
        ),
    }


# ── runner ────────────────────────────────────────────────────────

class WorkflowRunner:
    def __init__(
        self,
        orch: "Orchestrator",
        *,
        persist_dir: Optional[Path] = None,
        definitions: Optional[Dict[str, Workflow]] = None,
    ):
        self.orch = orch
        self.persist_dir = Path(persist_dir) if persist_dir else None
        self.definitions: Dict[str, Workflow] = dict(builtin_workflows())
        if definitions:
            self.definitions.update(definitions)
        self.runs: Dict[str, WorkflowRun] = {}
        self._lock = threading.RLock()
        if self.persist_dir:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            self._load_definitions()
            self._load_runs()

    # ── registry ──────────────────────────────────────────────────

    def register(self, workflow: Workflow) -> None:
        self.definitions[workflow.name] = workflow
        self._save_definition(workflow)

    def list_workflows(self) -> List[Dict[str, Any]]:
        return [
            {"name": w.name, "description": w.description, "steps": len(w.steps)}
            for w in self.definitions.values()
        ]

    def get(self, name: str) -> Optional[Workflow]:
        return self.definitions.get(name)

    # ── runs ──────────────────────────────────────────────────────

    def start(
        self,
        name: str,
        context: Optional[Dict[str, Any]] = None,
        *,
        run_id: Optional[str] = None,
    ) -> WorkflowRun:
        wf = self.definitions.get(name)
        if not wf:
            raise KeyError(f"Unknown workflow: {name}")
        run = WorkflowRun(
            id=run_id or f"wfr_{uuid.uuid4().hex[:10]}",
            workflow_name=name,
            user_id=getattr(self.orch, "user_id", None),
            status=WorkflowStatus.RUNNING,
            context=dict(context or {}),
            steps=[deepcopy(s) for s in (
                WorkflowStep.from_dict(s.to_dict()) for s in wf.steps
            )],
        )
        # deep copy steps cleanly
        run.steps = [WorkflowStep.from_dict(s.to_dict()) for s in wf.steps]
        with self._lock:
            self.runs[run.id] = run
            self._save_run(run)
        self._emit("workflow_started", run)
        self._execute(run)
        return run

    def resume(self, run_id: str, *, approve: Optional[bool] = None) -> WorkflowRun:
        run = self._require(run_id)
        if run.status == WorkflowStatus.WAITING_APPROVAL:
            if approve is False:
                run.status = WorkflowStatus.CANCELLED
                run.error = "Approval denied"
                run.completed_at = time.time()
                self._mark_remaining(run, StepStatus.CANCELLED)
                self._save_run(run)
                self._emit("workflow_cancelled", run)
                return run
            if approve is True:
                # complete current approval step
                if 0 <= run.current_index < len(run.steps):
                    step = run.steps[run.current_index]
                    if step.status == StepStatus.WAITING_APPROVAL:
                        step.status = StepStatus.COMPLETED
                        step.result = {"ok": True, "approved": True}
                        if step.save_as:
                            run.context[step.save_as] = step.result
                        run.current_index += 1
                run.status = WorkflowStatus.RUNNING
                run.approval_message = None
        elif run.status in (WorkflowStatus.PAUSED, WorkflowStatus.PENDING):
            run.status = WorkflowStatus.RUNNING
        else:
            if run.status in (WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED, WorkflowStatus.FAILED):
                return run
        self._save_run(run)
        self._execute(run)
        return run

    def cancel(self, run_id: str) -> WorkflowRun:
        run = self._require(run_id)
        run.status = WorkflowStatus.CANCELLED
        run.completed_at = time.time()
        self._mark_remaining(run, StepStatus.CANCELLED)
        self._save_run(run)
        self._emit("workflow_cancelled", run)
        return run

    def status(self, run_id: str) -> Dict[str, Any]:
        return self._require(run_id).to_dict()

    def list_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        items = sorted(self.runs.values(), key=lambda r: r.created_at, reverse=True)
        return [r.to_dict() for r in items[:limit]]

    # ── execution ─────────────────────────────────────────────────

    def _execute(self, run: WorkflowRun) -> None:
        try:
            from .tracing import get_tracer
            tracer = get_tracer()
        except Exception:
            tracer = None

        span_cm = None
        if tracer:
            span_cm = tracer.span(
                f"workflow.{run.workflow_name}",
                kind="internal",
                run_id=run.id,
            )
            span_cm.__enter__()

        try:
            while run.status == WorkflowStatus.RUNNING:
                if run.current_index >= len(run.steps):
                    run.status = WorkflowStatus.COMPLETED
                    run.completed_at = time.time()
                    self._checkpoint(run)
                    self._save_run(run)
                    self._emit("workflow_completed", run)
                    break

                step = run.steps[run.current_index]
                if step.status in (StepStatus.COMPLETED, StepStatus.SKIPPED):
                    run.current_index += 1
                    continue

                # conditional
                if not eval_when(step.when, run.context):
                    step.status = StepStatus.SKIPPED
                    run.current_index += 1
                    self._checkpoint(run)
                    self._save_run(run)
                    continue

                step.status = StepStatus.RUNNING
                step.attempts += 1
                self._save_run(run)
                self._emit("workflow_step_started", run, step=step.name)

                try:
                    result = self._run_step(run, step)
                    step.result = result if isinstance(result, dict) else {"ok": True, "reply": str(result)}
                    if step.save_as:
                        run.context[step.save_as] = step.result
                        # also flatten reply
                        if isinstance(step.result, dict) and "reply" in step.result:
                            run.context[f"{step.save_as}_reply"] = step.result["reply"]

                    if step.type == StepType.APPROVAL and step.status == StepStatus.WAITING_APPROVAL:
                        run.status = WorkflowStatus.WAITING_APPROVAL
                        run.approval_message = step.message or "Approval required"
                        self._checkpoint(run)
                        self._save_run(run)
                        self._emit("workflow_waiting_approval", run)
                        break

                    step.status = StepStatus.COMPLETED
                    run.current_index += 1
                    self._checkpoint(run)
                    self._save_run(run)
                    self._emit("workflow_step_completed", run, step=step.name)

                except Exception as e:
                    step.error = str(e)
                    if step.attempts <= step.max_retries:
                        step.status = StepStatus.PENDING
                        self._save_run(run)
                        continue
                    if step.on_failure == "continue":
                        step.status = StepStatus.FAILED
                        run.current_index += 1
                        self._checkpoint(run)
                        self._save_run(run)
                        continue
                    step.status = StepStatus.FAILED
                    run.status = WorkflowStatus.FAILED
                    run.error = str(e)
                    run.completed_at = time.time()
                    self._save_run(run)
                    self._emit("workflow_failed", run, error=str(e))
                    break
        finally:
            if span_cm is not None:
                try:
                    span_cm.__exit__(None, None, None)
                except Exception:
                    pass

    def _run_step(self, run: WorkflowRun, step: WorkflowStep) -> Dict[str, Any]:
        stype = step.type if isinstance(step.type, StepType) else StepType(step.type)

        if stype == StepType.SET:
            # objective is key=value pairs or JSON
            rendered = render_template(step.objective, run.context)
            if "=" in rendered and not rendered.strip().startswith("{"):
                k, _, v = rendered.partition("=")
                run.context[k.strip()] = v.strip()
            return {"ok": True, "context": dict(run.context)}

        if stype == StepType.APPROVAL:
            step.status = StepStatus.WAITING_APPROVAL
            return {"ok": True, "waiting": True, "message": step.message}

        if stype == StepType.TASK:
            objective = render_template(step.objective, run.context)
            return self.orch.route(str(objective))

        if stype == StepType.JOB:
            objective = render_template(step.objective, run.context)
            submitted = self.orch.submit_job(str(objective))
            job_id = submitted.get("job_id")
            # wait briefly for completion (sync for workflow determinism)
            if job_id:
                deadline = time.time() + 60
                while time.time() < deadline:
                    job = self.orch.jobs.get(job_id)
                    if job and job.status.value in ("completed", "failed", "cancelled"):
                        return {
                            "ok": job.status.value == "completed",
                            "job_id": job_id,
                            "result": job.result,
                            "error": job.error,
                        }
                    time.sleep(0.1)
            return submitted

        if stype == StepType.TOOL:
            tool = render_template(step.tool, run.context)
            args = render_template(step.tool_args, run.context)
            result = self.orch.tools.call(tool, **(args if isinstance(args, dict) else {}))
            return {"ok": True, "result": result}

        if stype == StepType.WORKFLOW:
            child_name = render_template(step.workflow, run.context)
            child = self.start(str(child_name), context=dict(run.context))
            # if child waits for approval, bubble up
            if child.status == WorkflowStatus.WAITING_APPROVAL:
                step.status = StepStatus.WAITING_APPROVAL
                run.approval_message = child.approval_message
                return {"ok": True, "child_run_id": child.id, "waiting": True}
            return {
                "ok": child.status == WorkflowStatus.COMPLETED,
                "child_run_id": child.id,
                "status": child.status.value,
                "context": child.context,
            }

        if stype == StepType.MEDIA:
            action = (render_template(step.media_action or step.objective, run.context) or "process").lower()
            path = render_template(step.media_path or step.objective, run.context)
            media = self.orch.media
            if "transcribe" in action or "listen" in action:
                return media.transcribe(path)
            if "describe" in action:
                return media.describe_image(path)
            if "ocr" in action:
                return media.ocr(path)
            return media.process(path)

        if stype == StepType.CONNECTOR:
            name = render_template(step.connector, run.context)
            action = render_template(step.connector_action or step.objective, run.context)
            params = render_template(step.connector_params, run.context)
            if not isinstance(params, dict):
                params = {}
            result = self.orch.connectors.execute(str(name), str(action), **params)
            return result.to_dict() if hasattr(result, "to_dict") else dict(result)

        if stype == StepType.PARALLEL:
            # sequential fallback that still records parallel intent (true threads optional)
            results = []
            for child_data in step.steps:
                child = WorkflowStep.from_dict(child_data)
                if not eval_when(child.when, run.context):
                    child.status = StepStatus.SKIPPED
                    results.append(child.to_dict())
                    continue
                try:
                    child.result = self._run_step(run, child)
                    child.status = StepStatus.COMPLETED
                except Exception as e:
                    child.error = str(e)
                    child.status = StepStatus.FAILED
                results.append(child.to_dict())
            return {"ok": all(r.get("status") == "completed" or r.get("status") == "skipped" for r in results), "steps": results}

        raise ValueError(f"Unknown step type: {stype}")

    # ── persistence ───────────────────────────────────────────────

    def _def_path(self, name: str) -> Optional[Path]:
        if not self.persist_dir:
            return None
        return self.persist_dir / "definitions" / f"{name}.json"

    def _run_path(self, run_id: str) -> Optional[Path]:
        if not self.persist_dir:
            return None
        return self.persist_dir / "runs" / f"{run_id}.json"

    def _save_definition(self, wf: Workflow) -> None:
        path = self._def_path(wf.name)
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(wf.to_dict(), indent=2), encoding="utf-8")

    def _save_run(self, run: WorkflowRun) -> None:
        run.touch()
        path = self._run_path(run.id)
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(run.to_dict(), indent=2), encoding="utf-8")

    def _load_definitions(self) -> None:
        if not self.persist_dir:
            return
        ddir = self.persist_dir / "definitions"
        if not ddir.exists():
            return
        for path in ddir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                wf = Workflow.from_dict(data)
                self.definitions[wf.name] = wf
            except Exception:
                continue

    def _load_runs(self) -> None:
        if not self.persist_dir:
            return
        rdir = self.persist_dir / "runs"
        if not rdir.exists():
            return
        for path in rdir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                run = WorkflowRun.from_dict(data)
                # interrupted running → paused for recovery
                if run.status == WorkflowStatus.RUNNING:
                    run.status = WorkflowStatus.PAUSED
                self.runs[run.id] = run
            except Exception:
                continue

    def _checkpoint(self, run: WorkflowRun) -> None:
        run.checkpoint = {
            "index": run.current_index,
            "status": run.status.value,
            "context_keys": list(run.context.keys()),
            "ts": time.time(),
        }

    def _mark_remaining(self, run: WorkflowRun, status: StepStatus) -> None:
        for i, step in enumerate(run.steps):
            if i >= run.current_index and step.status not in (
                StepStatus.COMPLETED, StepStatus.SKIPPED, StepStatus.FAILED
            ):
                step.status = status

    def _require(self, run_id: str) -> WorkflowRun:
        with self._lock:
            run = self.runs.get(run_id)
            if not run and self.persist_dir:
                path = self._run_path(run_id)
                if path and path.exists():
                    run = WorkflowRun.from_dict(json.loads(path.read_text(encoding="utf-8")))
                    self.runs[run.id] = run
            if not run:
                raise KeyError(f"Unknown workflow run: {run_id}")
            return run

    def _emit(self, kind: str, run: WorkflowRun, **extra: Any) -> None:
        try:
            from .events import EventType
            payload = {
                "run_id": run.id,
                "workflow": run.workflow_name,
                "status": run.status.value,
                **extra,
            }
            mapping = {
                "workflow_started": EventType.WORKFLOW_STARTED,
                "workflow_step_started": EventType.WORKFLOW_STEP_STARTED,
                "workflow_step_completed": EventType.WORKFLOW_STEP_COMPLETED,
                "workflow_waiting_approval": EventType.WORKFLOW_WAITING_APPROVAL,
                "workflow_completed": EventType.WORKFLOW_COMPLETED,
                "workflow_failed": EventType.WORKFLOW_FAILED,
                "workflow_cancelled": EventType.WORKFLOW_CANCELLED,
            }
            event = mapping.get(kind, EventType.NOTE)
            if event is EventType.NOTE:
                payload = {"kind": kind, **payload}
            self.orch.events.emit(event, payload, source="workflow")
        except Exception:
            pass
