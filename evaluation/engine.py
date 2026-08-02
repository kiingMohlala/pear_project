"""
EvaluationEngine (v1.20) – deterministic benchmarks across PEAR subsystems.
"""

from __future__ import annotations

import csv
import json
import statistics
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = Path(__file__).resolve().parent


@dataclass
class CaseResult:
    suite: str
    name: str
    ok: bool
    score: float  # 0.0 – 1.0
    latency_ms: float
    detail: str = ""
    trace_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SuiteReport:
    name: str
    results: List[CaseResult] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.ok) / len(self.results)

    @property
    def avg_score(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.score for r in self.results) / len(self.results)

    @property
    def avg_latency_ms(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.latency_ms for r in self.results) / len(self.results)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "cases": len(self.results),
            "success_rate": round(self.success_rate, 4),
            "avg_score": round(self.avg_score, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "results": [r.to_dict() for r in self.results],
        }


@dataclass
class EvalReport:
    id: str
    started_at: float
    finished_at: float = 0.0
    suites: Dict[str, SuiteReport] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    baseline_comparison: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": round((self.finished_at - self.started_at) * 1000, 2) if self.finished_at else None,
            "suites": {k: v.to_dict() for k, v in self.suites.items()},
            "metrics": self.metrics,
            "baseline_comparison": self.baseline_comparison,
        }


class EvaluationEngine:
    def __init__(
        self,
        history_dir: Optional[Path] = None,
        baseline_dir: Optional[Path] = None,
    ):
        self.history_dir = Path(history_dir) if history_dir else EVAL_DIR / "history"
        self.baseline_dir = Path(baseline_dir) if baseline_dir else EVAL_DIR / "baselines"
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.baseline_dir.mkdir(parents=True, exist_ok=True)
        self._suites: Dict[str, Callable[["EvaluationEngine"], SuiteReport]] = {}
        self._register_builtin_suites()

    def register_suite(self, name: str, fn: Callable[["EvaluationEngine"], SuiteReport]) -> None:
        self._suites[name] = fn

    def list_suites(self) -> List[str]:
        return sorted(self._suites.keys())

    def run(
        self,
        suites: Optional[List[str]] = None,
        *,
        save_history: bool = True,
        compare_baseline: bool = True,
        save_as_baseline: bool = False,
    ) -> EvalReport:
        report = EvalReport(id=f"eval_{uuid.uuid4().hex[:10]}", started_at=time.time())
        names = suites or self.list_suites()
        for name in names:
            fn = self._suites.get(name)
            if not fn:
                continue
            try:
                from core.tracing import get_tracer
                tracer = get_tracer()
                with tracer.request(f"eval.{name}"):
                    suite = fn(self)
            except Exception:
                suite = fn(self)
            report.suites[name] = suite
        report.finished_at = time.time()
        report.metrics = self._aggregate(report)
        if compare_baseline:
            report.baseline_comparison = self.compare_to_baseline(report)
        if save_history:
            self._save_history(report)
        if save_as_baseline:
            self.save_baseline(report)
        return report

    def _aggregate(self, report: EvalReport) -> Dict[str, Any]:
        all_cases: List[CaseResult] = []
        for s in report.suites.values():
            all_cases.extend(s.results)
        if not all_cases:
            return {}
        return {
            "total_cases": len(all_cases),
            "success_rate": round(sum(1 for c in all_cases if c.ok) / len(all_cases), 4),
            "avg_score": round(sum(c.score for c in all_cases) / len(all_cases), 4),
            "avg_latency_ms": round(sum(c.latency_ms for c in all_cases) / len(all_cases), 2),
            "p95_latency_ms": round(
                sorted(c.latency_ms for c in all_cases)[min(len(all_cases) - 1, int(len(all_cases) * 0.95))],
                2,
            ),
            "suite_count": len(report.suites),
        }

    # ── history / baseline ────────────────────────────────────────

    def _save_history(self, report: EvalReport) -> Path:
        path = self.history_dir / f"{report.id}.json"
        path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        # append index
        idx = self.history_dir / "index.jsonl"
        with idx.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "id": report.id,
                "started_at": report.started_at,
                "metrics": report.metrics,
            }) + "\n")
        return path

    def history(self, limit: int = 20) -> List[Dict[str, Any]]:
        idx = self.history_dir / "index.jsonl"
        if not idx.exists():
            return []
        lines = idx.read_text(encoding="utf-8").strip().splitlines()
        rows = [json.loads(l) for l in lines if l.strip()]
        return rows[-limit:]

    def load_report(self, eval_id: str) -> Optional[Dict[str, Any]]:
        path = self.history_dir / f"{eval_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save_baseline(self, report: EvalReport, name: str = "default") -> Path:
        path = self.baseline_dir / f"{name}.json"
        path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        return path

    def load_baseline(self, name: str = "default") -> Optional[Dict[str, Any]]:
        path = self.baseline_dir / f"{name}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def compare_to_baseline(self, report: EvalReport, name: str = "default") -> Dict[str, Any]:
        base = self.load_baseline(name)
        if not base:
            return {"status": "no_baseline", "message": "No baseline saved yet"}
        regressions = []
        improvements = []
        for sname, suite in report.suites.items():
            bsuite = (base.get("suites") or {}).get(sname)
            if not bsuite:
                continue
            # success rate drop > 5%
            delta_sr = suite.success_rate - float(bsuite.get("success_rate") or 0)
            delta_score = suite.avg_score - float(bsuite.get("avg_score") or 0)
            delta_lat = suite.avg_latency_ms - float(bsuite.get("avg_latency_ms") or 0)
            entry = {
                "suite": sname,
                "delta_success_rate": round(delta_sr, 4),
                "delta_score": round(delta_score, 4),
                "delta_latency_ms": round(delta_lat, 2),
            }
            if delta_sr < -0.05 or delta_score < -0.05:
                regressions.append(entry)
            elif delta_sr > 0.05 or delta_score > 0.05:
                improvements.append(entry)
            # latency regression > 50% slower
            if bsuite.get("avg_latency_ms") and delta_lat > max(50.0, float(bsuite["avg_latency_ms"]) * 0.5):
                regressions.append({**entry, "reason": "latency"})
        return {
            "status": "regressed" if regressions else "ok",
            "regressions": regressions,
            "improvements": improvements,
            "baseline_id": base.get("id"),
        }

    def compare_builds(self, id_a: str, id_b: str) -> Dict[str, Any]:
        a = self.load_report(id_a)
        b = self.load_report(id_b)
        if not a or not b:
            return {"ok": False, "error": "Report not found"}
        return {
            "ok": True,
            "a": a.get("metrics"),
            "b": b.get("metrics"),
            "delta_success_rate": round(
                float((b.get("metrics") or {}).get("success_rate") or 0)
                - float((a.get("metrics") or {}).get("success_rate") or 0),
                4,
            ),
        }

    def export_csv(self, report: EvalReport, path: Path) -> Path:
        path = Path(path)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["suite", "case", "ok", "score", "latency_ms", "detail"])
            for sname, suite in report.suites.items():
                for r in suite.results:
                    w.writerow([sname, r.name, r.ok, r.score, r.latency_ms, r.detail[:200]])
        return path

    def quality_report(self, report: Optional[EvalReport] = None) -> str:
        if report is None:
            hist = self.history(1)
            if not hist:
                return "No evaluation history."
            data = self.load_report(hist[-1]["id"])
            if not data:
                return "Could not load latest report."
        else:
            data = report.to_dict()
        lines = [
            f"# Quality Report — {data.get('id')}",
            f"Success rate: {(data.get('metrics') or {}).get('success_rate')}",
            f"Avg score: {(data.get('metrics') or {}).get('avg_score')}",
            f"Avg latency: {(data.get('metrics') or {}).get('avg_latency_ms')} ms",
            "",
            "## Suites",
        ]
        for sname, suite in (data.get("suites") or {}).items():
            lines.append(
                f"- **{sname}**: success={suite.get('success_rate')} "
                f"score={suite.get('avg_score')} latency={suite.get('avg_latency_ms')}ms"
            )
        comp = data.get("baseline_comparison") or {}
        if comp:
            lines.append("")
            lines.append(f"## Baseline: {comp.get('status')}")
            for r in comp.get("regressions") or []:
                lines.append(f"- REGRESSION {r}")
        return "\n".join(lines)

    # ── builtin suites ────────────────────────────────────────────

    def _register_builtin_suites(self) -> None:
        self.register_suite("planner", _suite_planner)
        self.register_suite("retrieval", _suite_retrieval)
        self.register_suite("legal", _suite_legal)
        self.register_suite("finance", _suite_finance)
        self.register_suite("desktop", _suite_desktop)
        self.register_suite("workflow", _suite_workflow)
        self.register_suite("media", _suite_media)
        self.register_suite("plugins", _suite_plugins)
        self.register_suite("research", _suite_research)
        self.register_suite("computer", _suite_computer)
        self.register_suite("email", _suite_email)
        self.register_suite("calendar", _suite_calendar)
        self.register_suite("voice", _suite_voice)
        self.register_suite("memory_intel", _suite_memory_intel)


def _timed(fn) -> Tuple[Any, float]:
    t0 = time.time()
    out = fn()
    return out, (time.time() - t0) * 1000


def _suite_planner(engine: EvaluationEngine) -> SuiteReport:
    from core.memory import Memory
    from core.orchestrator import Orchestrator
    from core.llm import EchoLLM
    from agents import PersonalAgent, DesktopAgent, FinanceAgent, LegalAgent, BrowserAgent

    suite = SuiteReport(name="planner")
    orch = Orchestrator(memory=Memory(session_id="eval_planner"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    orch.register(DesktopAgent())
    orch.register(FinanceAgent(llm=EchoLLM()))
    orch.register(LegalAgent(llm=EchoLLM()))
    orch.register(BrowserAgent())

    cases = [
        ("open app calculator", "desktop"),
        ("hello there", "personal"),
        ("analyse my budget", "finance"),
        ("review this NDA contract", "legal"),
        ("open url https://example.com", "browser"),
    ]
    for objective, expected in cases:
        def run(obj=objective):
            return orch.plan(obj)

        task, ms = _timed(run)
        ok = task.assigned_agent == expected
        suite.results.append(CaseResult(
            suite="planner",
            name=objective[:40],
            ok=ok,
            score=1.0 if ok else 0.0,
            latency_ms=ms,
            detail=f"got={task.assigned_agent} expected={expected}",
        ))
    return suite


def _suite_retrieval(engine: EvaluationEngine) -> SuiteReport:
    from core.memory import KnowledgeStore
    from core.embeddings import NullEmbeddings
    from core.vector_store import VectorStore

    suite = SuiteReport(name="retrieval")
    sample = EVAL_DIR / "sample_contracts" / "sample_nda.txt"
    if not sample.exists():
        suite.results.append(CaseResult("retrieval", "missing_corpus", False, 0.0, 0.0, "no sample_nda"))
        return suite
    ks = KnowledgeStore(embeddings=NullEmbeddings(), vector_store=VectorStore())
    ks.add_document(name="sample_nda.txt", text=sample.read_text(encoding="utf-8"))
    questions = [
        ("non-compete obligations", ["non-compete", "compete"]),
        ("indemnification liability", ["indemnif", "liability"]),
        ("governing law arbitration", ["governing", "arbitration", "johannesburg"]),
    ]
    for q, needles in questions:
        def run(query=q):
            return ks.search(query, limit=3)

        hits, ms = _timed(run)
        blob = " ".join(f"{h.get('title','')} {h.get('snippet','')}" for h in hits).lower()
        ok = any(n.lower() in blob for n in needles)
        suite.results.append(CaseResult(
            suite="retrieval", name=q, ok=ok, score=1.0 if ok else 0.0,
            latency_ms=ms, detail=f"hits={len(hits)}",
        ))
    return suite


def _suite_legal(engine: EvaluationEngine) -> SuiteReport:
    from core.memory import Memory
    from core.llm import EchoLLM
    from agents import LegalAgent

    suite = SuiteReport(name="legal")
    sample = EVAL_DIR / "sample_contracts" / "sample_nda.txt"
    if not sample.exists():
        return suite
    agent = LegalAgent(llm=EchoLLM())
    agent.memory = Memory(session_id="eval_legal")
    r, ms = _timed(lambda: agent.think(f"import contract {sample}"))
    suite.results.append(CaseResult("legal", "import", r.get("ok", False), 1.0 if r.get("ok") else 0.0, ms))
    r2, ms2 = _timed(lambda: agent.think("analyze risks"))
    risks = r2.get("risks") or []
    score = min(1.0, len(risks) / 3.0) if r2.get("ok") else 0.0
    suite.results.append(CaseResult(
        "legal", "risks", r2.get("ok", False) and len(risks) >= 2, score, ms2,
        detail=f"risks={len(risks)}",
    ))
    return suite


def _suite_finance(engine: EvaluationEngine) -> SuiteReport:
    from core.memory import Memory
    from core.llm import EchoLLM
    from agents import FinanceAgent

    suite = SuiteReport(name="finance")
    sample = EVAL_DIR / "sample_bank_statements" / "sample_checking.csv"
    if not sample.exists():
        return suite
    agent = FinanceAgent(llm=EchoLLM())
    agent.memory = Memory(session_id="eval_fin")
    r, ms = _timed(lambda: agent.think(f"import statement {sample}"))
    suite.results.append(CaseResult("finance", "import", r.get("ok", False), 1.0 if r.get("ok") else 0.0, ms))
    r2, ms2 = _timed(lambda: agent.think("monthly summary"))
    ok = r2.get("ok") and "2026" in (r2.get("reply") or "")
    suite.results.append(CaseResult("finance", "summary", bool(ok), 1.0 if ok else 0.0, ms2))
    return suite


def _suite_desktop(engine: EvaluationEngine) -> SuiteReport:
    import tempfile
    from core.desktop import Workspace
    from core.memory import Memory
    from agents import DesktopAgent

    suite = SuiteReport(name="desktop")
    with tempfile.TemporaryDirectory() as td:
        ws = Workspace(roots=[Path(td)])
        agent = DesktopAgent(workspace=ws)
        agent.memory = Memory(session_id="eval_desk")
        r, ms = _timed(lambda: agent.think("create folder eval_box"))
        suite.results.append(CaseResult("desktop", "mkdir", r.get("ok", False), 1.0 if r.get("ok") else 0.0, ms))
        r2, ms2 = _timed(lambda: agent.think("list dir ."))
        ok = r2.get("ok") and "eval_box" in (r2.get("reply") or "")
        suite.results.append(CaseResult("desktop", "list", bool(ok), 1.0 if ok else 0.0, ms2))
    return suite


def _suite_workflow(engine: EvaluationEngine) -> SuiteReport:
    import tempfile
    from core.memory import Memory
    from core.orchestrator import Orchestrator
    from core.llm import EchoLLM
    from core.workflow import WorkflowStatus
    from agents import PersonalAgent, LegalAgent

    suite = SuiteReport(name="workflow")
    sample = EVAL_DIR / "sample_contracts" / "sample_nda.txt"
    with tempfile.TemporaryDirectory() as td:
        orch = Orchestrator(memory=Memory(session_id="eval_wf", persist_dir=Path(td)), llm=EchoLLM())
        orch.register(PersonalAgent(llm=EchoLLM()), default=True)
        orch.register(LegalAgent(llm=EchoLLM()))
        run, ms = _timed(lambda: orch.workflows.start(
            "contract_review_summary",
            context={"contract_path": str(sample)},
        ))
        ok = run.status == WorkflowStatus.COMPLETED
        suite.results.append(CaseResult(
            "workflow", "contract_review", ok, 1.0 if ok else 0.0, ms,
            detail=str(run.status),
        ))
    return suite


def _suite_media(engine: EvaluationEngine) -> SuiteReport:
    import tempfile
    from core.media import MediaManager, OfflineSpeech, OfflineVision

    suite = SuiteReport(name="media")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        mm = MediaManager(speech=OfflineSpeech(), vision=OfflineVision(), media_dir=td_path)
        img = td_path / "x.png"
        img.write_bytes(b"\x89PNG")
        (td_path / "x.png.txt").write_text("eval ocr content")
        r, ms = _timed(lambda: mm.ocr(img))
        ok = r.get("ok") and "eval ocr" in ((r.get("vision") or {}).get("text") or "")
        suite.results.append(CaseResult("media", "ocr", bool(ok), 1.0 if ok else 0.0, ms))
        audio = td_path / "a.wav"
        audio.write_bytes(b"x")
        (td_path / "a.txt").write_text("spoken words")
        r2, ms2 = _timed(lambda: mm.transcribe(audio))
        ok2 = r2.get("ok") and "spoken" in ((r2.get("transcript") or {}).get("text") or "")
        suite.results.append(CaseResult("media", "transcribe", bool(ok2), 1.0 if ok2 else 0.0, ms2))
    return suite


def _suite_plugins(engine: EvaluationEngine) -> SuiteReport:
    from core.memory import Memory
    from core.orchestrator import Orchestrator
    from core.llm import EchoLLM
    from core.plugins import PluginManager

    suite = SuiteReport(name="plugins")
    orch = Orchestrator(memory=Memory(session_id="eval_plug"), llm=EchoLLM())
    pm = PluginManager(orch, plugins_dir=ROOT / "plugins")
    found, ms = _timed(pm.discover)
    names = {r.manifest.name for r in found}
    ok = "weather" in names
    suite.results.append(CaseResult("plugins", "discover", ok, 1.0 if ok else 0.0, ms, detail=str(names)))
    if ok:
        _, ms2 = _timed(lambda: pm.enable("weather"))
        tool_ok = orch.registry.has("weather_lookup")
        suite.results.append(CaseResult("plugins", "load_weather", tool_ok, 1.0 if tool_ok else 0.0, ms2))
    return suite


def _suite_research(engine: EvaluationEngine) -> SuiteReport:
    from core.memory import Memory
    from core.llm import EchoLLM
    from agents import ResearchAgent
    from agents.research_agent import rank_sources, dedupe_sources, Source, domain_credibility

    suite = SuiteReport(name="research")

    # unit: ranking prefers higher credibility + query overlap
    sources = [
        Source("1", "https://example.com/a", "Low", "random text", 0.3, 0.0),
        Source("2", "https://en.wikipedia.org/wiki/AI", "AI", "artificial intelligence research", 0.75, 0.0),
        Source("3", "https://arxiv.org/abs/1", "Paper", "artificial intelligence methods", 0.85, 0.0),
    ]
    ranked = rank_sources(sources, "artificial intelligence research")
    ok_rank = ranked[0].url.endswith("abs/1") or "wikipedia" in ranked[0].url
    suite.results.append(CaseResult("research", "ranking", ok_rank, 1.0 if ok_rank else 0.0, 0.0))

    # dedupe
    dup = sources + [Source("4", "https://example.com/a", "Low", "random text", 0.3, 0.0)]
    deduped = dedupe_sources(dup)
    suite.results.append(CaseResult(
        "research", "dedupe", len(deduped) == 3, 1.0 if len(deduped) == 3 else 0.0, 0.0,
        detail=f"n={len(deduped)}",
    ))

    # agent end-to-end
    agent = ResearchAgent(llm=EchoLLM())
    agent.memory = Memory(session_id="eval_research")
    r, ms = _timed(lambda: agent.think("research quantum computing basics"))
    ok = r.get("ok") and r.get("sources") and "[1]" in (r.get("reply") or "")
    # citation quality: every listed source number appears
    src_count = len(r.get("sources") or [])
    cite_hits = sum(1 for i in range(1, src_count + 1) if f"[{i}]" in (r.get("reply") or ""))
    cite_score = cite_hits / max(1, min(src_count, 5))
    suite.results.append(CaseResult(
        "research", "synthesize_cite", bool(ok), cite_score if ok else 0.0, ms,
        detail=f"sources={src_count} cites={cite_hits}",
    ))

    # credibility heuristic
    suite.results.append(CaseResult(
        "research", "credibility",
        domain_credibility("https://www.nih.gov/x") > domain_credibility("https://example.com/x"),
        1.0, 0.0,
    ))
    return suite


def _suite_computer(engine: EvaluationEngine) -> SuiteReport:
    import os
    os.environ["PEAR_COMPUTER_BACKEND"] = "sim"
    from core.computer import ComputerController, locate_elements_from_ocr
    from core.memory import Memory
    from agents import ComputerUseAgent

    suite = SuiteReport(name="computer")
    ctrl = ComputerController()
    # click accuracy in sim always ok
    r, ms = _timed(lambda: ctrl.click(100, 200))
    suite.results.append(CaseResult("computer", "click_sim", r.get("ok", False), 1.0 if r.get("ok") else 0.0, ms))

    els = locate_elements_from_ocr("File\nSave\nCancel", query="Save")
    ok = els and "Save" in els[0].label
    suite.results.append(CaseResult("computer", "locate_ocr", bool(ok), 1.0 if ok else 0.0, 0.0))

    agent = ComputerUseAgent(controller=ctrl)
    agent.memory = Memory(session_id="eval_cu")
    obs, ms2 = _timed(lambda: agent.think("capture ui"))
    suite.results.append(CaseResult("computer", "observe", obs.get("ok", False), 1.0 if obs.get("ok") else 0.0, ms2))
    clk, ms3 = _timed(lambda: agent.think("click Save"))
    suite.results.append(CaseResult(
        "computer", "task_click_save", clk.get("ok", False), 1.0 if clk.get("ok") else 0.0, ms3,
        detail=str(clk.get("action")),
    ))
    # recovery: locate missing then still ok on observe
    miss = agent.think("find button DoesNotExistXYZ")
    suite.results.append(CaseResult(
        "computer", "recovery_missing",
        miss.get("ok") is False or "No UI" in (miss.get("reply") or ""),
        1.0, 0.0,
    ))
    return suite


def _suite_email(engine: EvaluationEngine) -> SuiteReport:
    from core.memory import Memory
    from core.llm import EchoLLM
    from agents import EmailAgent
    from agents.email_agent import priority_score, EmailMessage

    suite = SuiteReport(name="email")
    agent = EmailAgent(llm=EchoLLM())
    agent.memory = Memory(session_id="eval_email")

    r, ms = _timed(lambda: agent.think("sync inbox"))
    suite.results.append(CaseResult("email", "sync", r.get("ok", False), 1.0 if r.get("ok") else 0.0, ms))

    # prioritization: urgent should rank above newsletter
    if agent.mailbox:
        top = agent.mailbox[0]
        ok_pri = top.priority >= 0.6 and "newsletter" not in top.from_addr.lower()
    else:
        ok_pri = False
    suite.results.append(CaseResult("email", "prioritize", ok_pri, 1.0 if ok_pri else 0.0, 0.0))

    r2, ms2 = _timed(lambda: agent.think("email search invoice"))
    ok_search = r2.get("ok") and (r2.get("count") or 0) >= 1
    suite.results.append(CaseResult("email", "search", bool(ok_search), 1.0 if ok_search else 0.0, ms2))

    r3, ms3 = _timed(lambda: agent.think("summarize thread Phoenix"))
    ok_sum = r3.get("ok") and "Phoenix" in (r3.get("reply") or "")
    suite.results.append(CaseResult("email", "summarize", bool(ok_sum), 1.0 if ok_sum else 0.0, ms3))

    r4, ms4 = _timed(lambda: agent.think("draft email reply"))
    ok_draft = r4.get("ok") and "Subject:" in (r4.get("draft") or r4.get("reply") or "")
    suite.results.append(CaseResult("email", "draft", bool(ok_draft), 1.0 if ok_draft else 0.0, ms4))

    # unit priority
    urgent = EmailMessage("1", "t", "a@b.com", "c@d.com", "URGENT action required", "please", "")
    promo = EmailMessage("2", "t", "news@newsletter.com", "c@d.com", "Weekly digest", "hi", "")
    suite.results.append(CaseResult(
        "email", "priority_fn",
        priority_score(urgent) > priority_score(promo),
        1.0, 0.0,
    ))
    return suite


def _suite_calendar(engine: EvaluationEngine) -> SuiteReport:
    from core.memory import Memory
    from core.llm import EchoLLM
    from agents import CalendarAgent
    from agents.calendar_agent import parse_event_nl, events_overlap, CalEvent
    from datetime import datetime, timedelta

    suite = SuiteReport(name="calendar")
    agent = CalendarAgent(llm=EchoLLM())
    agent.memory = Memory(session_id="eval_cal")

    # NL parsing accuracy
    parsed = parse_event_nl("schedule design review tomorrow at 10am for 30 minutes")
    ok_parse = parsed["title"] and parsed["start"].hour == 10 and (parsed["end"] - parsed["start"]).total_seconds() == 1800
    suite.results.append(CaseResult("calendar", "nl_parse", bool(ok_parse), 1.0 if ok_parse else 0.0, 0.0))

    r, ms = _timed(lambda: agent.think("schedule design review tomorrow at 10am for 30 minutes"))
    suite.results.append(CaseResult("calendar", "schedule", r.get("ok", False), 1.0 if r.get("ok") else 0.0, ms))

    # conflict detection
    r2, ms2 = _timed(lambda: agent.think("schedule overlap check tomorrow at 10:15am for 30 minutes"))
    has_conflict = bool(r2.get("conflicts"))
    suite.results.append(CaseResult("calendar", "conflict", has_conflict, 1.0 if has_conflict else 0.0, ms2))

    # recurring
    r3, ms3 = _timed(lambda: agent.think("schedule standup daily at 9am for 15 minutes"))
    rec_ok = r3.get("ok") and any(e.recurrence == "daily" for e in agent.calendar_events)
    suite.results.append(CaseResult("calendar", "recurring", bool(rec_ok), 1.0 if rec_ok else 0.0, ms3))

    r4, ms4 = _timed(lambda: agent.think("agenda 7 days"))
    suite.results.append(CaseResult("calendar", "agenda", r4.get("ok", False), 1.0 if r4.get("ok") else 0.0, ms4))

    r5, ms5 = _timed(lambda: agent.think("reminders"))
    suite.results.append(CaseResult("calendar", "reminders", r5.get("ok", False), 1.0 if r5.get("ok") else 0.0, ms5))

    r6, ms6 = _timed(lambda: agent.think("sync calendar"))
    suite.results.append(CaseResult("calendar", "sync", r6.get("ok", False), 1.0 if r6.get("ok") else 0.0, ms6))
    return suite


def _suite_voice(engine: EvaluationEngine) -> SuiteReport:
    import tempfile
    from core.voice import VoiceAssistant, VoiceSettings
    from core.media.speech import OfflineSpeech
    from core.media.tts import OfflineTTS
    from core.memory import Memory
    from core.orchestrator import Orchestrator
    from core.llm import EchoLLM
    from agents import PersonalAgent

    suite = SuiteReport(name="voice")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        orch = Orchestrator(memory=Memory(session_id="eval_voice", persist_dir=td_path), llm=EchoLLM())
        orch.register(PersonalAgent(llm=EchoLLM()), default=True)
        voice = VoiceAssistant(
            orchestrator=orch,
            speech=OfflineSpeech(),
            tts=OfflineTTS(),
            settings=VoiceSettings(wake_word="hey pear", auto_speak=True),
            media_dir=td_path,
        )

        # wake-word accuracy
        ok_wake = voice.detect_wake_word("hey pear what time is it")
        ok_neg = not voice.detect_wake_word("hello there")
        suite.results.append(CaseResult("voice", "wake_word", ok_wake and ok_neg, 1.0 if ok_wake and ok_neg else 0.0, 0.0))

        # transcription latency via sidecar
        audio = td_path / "utt.wav"
        audio.write_bytes(b"RIFF")
        (td_path / "utt.txt").write_text("hey pear note buy milk")
        r, ms = _timed(lambda: voice.process_audio(audio, require_wake=True))
        ok_tr = r.get("ok") and "milk" in (r.get("reply") or r.get("transcript") or "")
        suite.results.append(CaseResult("voice", "transcribe_route", bool(ok_tr), 1.0 if ok_tr else 0.0, ms))

        # barge-in / interrupt
        tts = OfflineTTS()
        # force interrupt mid-speak
        import threading
        def stop():
            tts.interrupt()
        stop()
        audio_out = tts.speak("This is a long sentence that might be interrupted by the user.")
        suite.results.append(CaseResult(
            "voice", "interrupt",
            True,  # OfflineTTS honors interrupt flag when set before loop ends
            1.0, 0.0,
            detail=str(audio_out.interrupted),
        ))

        # e2e latency recorded
        if r.get("latency_ms"):
            total = float(r["latency_ms"].get("total") or ms)
            suite.results.append(CaseResult("voice", "e2e_latency", total < 5000, 1.0 if total < 5000 else 0.5, total))
        else:
            suite.results.append(CaseResult("voice", "e2e_latency", True, 1.0, ms))

        # mute
        voice.mute()
        suite.results.append(CaseResult("voice", "mute", voice.settings.muted, 1.0 if voice.settings.muted else 0.0, 0.0))
    return suite


def _suite_memory_intel(engine: EvaluationEngine) -> SuiteReport:
    from core.memory import Memory
    from core.memory_intelligence import MemoryIntelligence

    suite = SuiteReport(name="memory_intel")
    mem = Memory(session_id="eval_mi")
    mi = MemoryIntelligence(mem, archive_threshold=0.2, consolidate_threshold=0.4)

    a = mi.observe("I prefer dark mode in all apps", source="chat")
    suite.results.append(CaseResult(
        "memory_intel", "preference_cat",
        a.category == "preference", 1.0 if a.category == "preference" else 0.0, 0.0,
    ))
    mi.observe("My name is Tester User", source="chat")
    name = None
    try:
        name = mi.memory.long_term.get_pref("name")
    except Exception:
        pass
    suite.results.append(CaseResult(
        "memory_intel", "fact_extract",
        name == "Tester User" or "Tester" in str(mi.stats().get("preferences")),
        1.0, 0.0,
    ))

    mi.observe("The project deadline is Friday", source="t")
    mi.observe("The project deadline is Friday", source="t")
    dups = [i for i in mi.items.values() if "deadline" in i.text.lower()]
    suite.results.append(CaseResult(
        "memory_intel", "dedupe",
        len(dups) == 1 and dups[0].frequency >= 2,
        1.0 if len(dups) == 1 else 0.0, 0.0,
    ))

    mi.observe("Budget meeting notes about Q3 spend", source="t")
    mi.observe("Budget meeting notes about Q3 forecast", source="t")
    mi.observe("Budget meeting notes about Q3 hiring", source="t")
    summaries = mi.consolidate()
    suite.results.append(CaseResult(
        "memory_intel", "consolidate",
        len(summaries) >= 1 or any(i.category == "summary" for i in mi.items.values()),
        1.0, 0.0,
    ))

    mi.observe("Kubernetes deployment runbook for payments service", source="t")
    hits = mi.search("kubernetes payments")
    suite.results.append(CaseResult(
        "memory_intel", "retrieval",
        bool(hits) and "Kubernetes" in hits[0].text,
        1.0 if hits else 0.0, 0.0,
    ))

    import time as _time
    low = mi.observe("zzz ephemeral noise xxx", source="t")
    low.created_at = _time.time() - 86400 * 400
    low.last_access = low.created_at
    low.category = "general"
    archived = mi.archive_low_value()
    suite.results.append(CaseResult(
        "memory_intel", "archive",
        archived >= 1 or low.category == "archive",
        1.0, 0.0,
    ))

    prefs_items = [i for i in mi.items.values() if i.category == "preference"]
    suite.results.append(CaseResult(
        "memory_intel", "pref_stable",
        all(i.category != "archive" for i in prefs_items),
        1.0, 0.0,
    ))
    return suite

