"""
Learning & Self-Optimization (v2.10).

Consumes execution history and produces *recommendations* (not silent
behavior changes). Deterministic offline behavior for tests.
"""

from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .orchestrator import Orchestrator


@dataclass
class Recommendation:
    id: str
    category: str  # planner | retrieval | workflow | collaboration | general
    title: str
    detail: str
    confidence: float
    evidence: Dict[str, Any] = field(default_factory=dict)
    reversible: bool = True
    applied: bool = False
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Recommendation":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


class LearningEngine:
    def __init__(self, orchestrator: "Orchestrator", persist_dir: Optional[Path] = None):
        self.orch = orchestrator
        if persist_dir is None:
            base = getattr(getattr(orchestrator, "memory", None), "persist_dir", None)
            persist_dir = Path(base) / "learning" if base else Path.home() / ".pear" / "learning"
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.recommendations: List[Recommendation] = []
        self.history: List[Dict[str, Any]] = []
        self.routing_stats: Dict[str, Dict[str, float]] = defaultdict(lambda: {
            "success": 0, "fail": 0, "latency_ms": 0.0, "n": 0,
        })
        self.collab_stats: Dict[str, Dict[str, float]] = defaultdict(lambda: {
            "success": 0, "fail": 0, "disagreement": 0.0, "n": 0,
        })
        self.workflow_step_stats: Dict[str, Dict[str, float]] = defaultdict(lambda: {
            "fail": 0, "n": 0, "latency_ms": 0.0,
        })
        self.retrieval_feedback: Dict[str, float] = defaultdict(float)  # term -> usefulness
        self._load()

    # ── persistence ───────────────────────────────────────────────

    def _state_path(self) -> Path:
        return self.persist_dir / "learning_state.json"

    def _save(self) -> None:
        data = {
            "recommendations": [r.to_dict() for r in self.recommendations[-200:]],
            "history": self.history[-200:],
            "routing_stats": dict(self.routing_stats),
            "collab_stats": dict(self.collab_stats),
            "workflow_step_stats": dict(self.workflow_step_stats),
            "retrieval_feedback": dict(self.retrieval_feedback),
        }
        self._state_path().write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load(self) -> None:
        p = self._state_path()
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            self.recommendations = [Recommendation.from_dict(r) for r in data.get("recommendations") or []]
            self.history = list(data.get("history") or [])
            for k, v in (data.get("routing_stats") or {}).items():
                self.routing_stats[k].update(v)
            for k, v in (data.get("collab_stats") or {}).items():
                self.collab_stats[k].update(v)
            for k, v in (data.get("workflow_step_stats") or {}).items():
                self.workflow_step_stats[k].update(v)
            self.retrieval_feedback.update(data.get("retrieval_feedback") or {})
        except Exception:
            pass

    def _span(self, name: str, **attrs):
        try:
            from .tracing import get_tracer
            return get_tracer().span(name, kind="learning", **attrs)
        except Exception:
            from contextlib import nullcontext
            return nullcontext()

    # ── observe signals ───────────────────────────────────────────

    def observe_route(self, agent: str, ok: bool, latency_ms: float = 0.0, objective: str = "") -> None:
        st = self.routing_stats[agent or "unknown"]
        st["n"] += 1
        st["latency_ms"] += latency_ms
        if ok:
            st["success"] += 1
        else:
            st["fail"] += 1

    def observe_collab(self, mode: str, ok: bool, disagreement: float = 0.0) -> None:
        st = self.collab_stats[mode or "unknown"]
        st["n"] += 1
        st["disagreement"] += disagreement
        if ok:
            st["success"] += 1
        else:
            st["fail"] += 1

    def observe_workflow_step(self, step_name: str, ok: bool, latency_ms: float = 0.0) -> None:
        st = self.workflow_step_stats[step_name or "step"]
        st["n"] += 1
        st["latency_ms"] += latency_ms
        if not ok:
            st["fail"] += 1

    def observe_retrieval_feedback(self, query: str, useful: bool) -> None:
        for term in (query or "").lower().split():
            if len(term) > 3:
                self.retrieval_feedback[term] += 1.0 if useful else -0.5

    def ingest_evaluation(self, report: Dict[str, Any]) -> None:
        """Pull metrics from EvaluationEngine report dict."""
        metrics = report.get("metrics") or {}
        self.history.append({
            "type": "evaluation",
            "id": report.get("id"),
            "metrics": metrics,
            "ts": time.time(),
        })
        suites = report.get("suites") or {}
        for name, suite in suites.items():
            sr = float(suite.get("success_rate") or 0)
            if sr < 0.8:
                self._add_rec(
                    category="general",
                    title=f"Evaluation suite '{name}' below 80%",
                    detail=f"Success rate={sr}. Investigate regressions in {name}.",
                    confidence=min(0.9, 0.5 + (0.8 - sr)),
                    evidence={"suite": name, "success_rate": sr},
                )

    # ── analysis ──────────────────────────────────────────────────

    def analyze(self) -> List[Recommendation]:
        """Generate fresh recommendations from accumulated stats."""
        with self._span("learning.analyze"):
            before = len(self.recommendations)
            self._analyze_planner()
            self._analyze_collaboration()
            self._analyze_workflows()
            self._analyze_retrieval()
            self._analyze_goals()
            self._save()
            return self.recommendations[before:]

    def _add_rec(
        self,
        category: str,
        title: str,
        detail: str,
        confidence: float,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> Recommendation:
        # dedupe by title
        for r in self.recommendations:
            if r.title == title and not r.applied:
                r.confidence = max(r.confidence, confidence)
                r.detail = detail
                r.evidence = evidence or r.evidence
                return r
        rec = Recommendation(
            id=f"rec_{uuid.uuid4().hex[:10]}",
            category=category,
            title=title,
            detail=detail,
            confidence=round(max(0.0, min(1.0, confidence)), 3),
            evidence=evidence or {},
        )
        self.recommendations.append(rec)
        self.history.append({"type": "recommendation", "id": rec.id, "title": title, "ts": time.time()})
        return rec

    def _analyze_planner(self) -> None:
        for agent, st in self.routing_stats.items():
            n = st["n"]
            if n < 3:
                continue
            success_rate = st["success"] / n
            avg_lat = st["latency_ms"] / n if n else 0
            if success_rate < 0.6:
                self._add_rec(
                    "planner",
                    f"Reduce routing to agent '{agent}'",
                    f"Success rate {success_rate:.0%} over {n} calls. Prefer alternate agents or add preconditions.",
                    confidence=min(0.95, 0.55 + (0.6 - success_rate)),
                    evidence={"agent": agent, "success_rate": success_rate, "n": n},
                )
            elif success_rate > 0.9 and n >= 5:
                self._add_rec(
                    "planner",
                    f"Prefer agent '{agent}' for similar tasks",
                    f"High success rate {success_rate:.0%} over {n} calls (avg latency {avg_lat:.0f}ms).",
                    confidence=min(0.9, 0.5 + success_rate * 0.3),
                    evidence={"agent": agent, "success_rate": success_rate, "avg_latency_ms": avg_lat},
                )
            if avg_lat > 2000 and n >= 3:
                self._add_rec(
                    "planner",
                    f"Agent '{agent}' is slow",
                    f"Average latency {avg_lat:.0f}ms. Consider caching or background jobs.",
                    confidence=0.65,
                    evidence={"agent": agent, "avg_latency_ms": avg_lat},
                )

    def _analyze_collaboration(self) -> None:
        best_mode, best_rate = None, -1.0
        for mode, st in self.collab_stats.items():
            n = st["n"]
            if n < 2:
                continue
            rate = st["success"] / n
            avg_dis = st["disagreement"] / n
            if rate > best_rate:
                best_rate, best_mode = rate, mode
            if avg_dis > 0.6 and mode == "parallel":
                self._add_rec(
                    "collaboration",
                    "Prefer consensus over parallel when disagreement is high",
                    f"Parallel mode average disagreement={avg_dis:.2f}. Consensus may improve quality.",
                    confidence=min(0.85, 0.5 + avg_dis * 0.4),
                    evidence={"mode": mode, "avg_disagreement": avg_dis, "n": n},
                )
            if rate < 0.5:
                self._add_rec(
                    "collaboration",
                    f"Collaboration mode '{mode}' underperforming",
                    f"Success rate {rate:.0%} over {n} runs.",
                    confidence=0.7,
                    evidence={"mode": mode, "success_rate": rate},
                )
        if best_mode and best_rate >= 0.8:
            self._add_rec(
                "collaboration",
                f"Default collaboration mode → {best_mode}",
                f"Best historical success rate {best_rate:.0%}.",
                confidence=min(0.88, 0.5 + best_rate * 0.3),
                evidence={"mode": best_mode, "success_rate": best_rate},
            )

    def _analyze_workflows(self) -> None:
        for step, st in self.workflow_step_stats.items():
            n = st["n"]
            if n < 2:
                continue
            fail_rate = st["fail"] / n
            avg_lat = st["latency_ms"] / n
            if fail_rate > 0.3:
                self._add_rec(
                    "workflow",
                    f"Workflow step '{step}' fails often",
                    f"Failure rate {fail_rate:.0%} over {n} runs. Add retries or preconditions.",
                    confidence=min(0.9, 0.5 + fail_rate),
                    evidence={"step": step, "fail_rate": fail_rate, "n": n},
                )
            if avg_lat > 3000:
                self._add_rec(
                    "workflow",
                    f"Workflow step '{step}' is slow",
                    f"Average {avg_lat:.0f}ms. Consider background job execution.",
                    confidence=0.6,
                    evidence={"step": step, "avg_latency_ms": avg_lat},
                )

    def _analyze_retrieval(self) -> None:
        # terms with strong negative feedback
        bad = [(t, s) for t, s in self.retrieval_feedback.items() if s <= -1.0]
        bad.sort(key=lambda x: x[1])
        for term, score in bad[:5]:
            self._add_rec(
                "retrieval",
                f"Retrieval weak for term '{term}'",
                f"Cumulative usefulness score={score}. Re-index or boost alternate sources.",
                confidence=min(0.8, 0.4 + abs(score) * 0.1),
                evidence={"term": term, "usefulness": score},
            )
        good = [(t, s) for t, s in self.retrieval_feedback.items() if s >= 2.0]
        if good:
            terms = ", ".join(t for t, _ in sorted(good, key=lambda x: -x[1])[:5])
            self._add_rec(
                "retrieval",
                "Boost historically useful retrieval terms",
                f"Terms with positive feedback: {terms}",
                confidence=0.6,
                evidence={"terms": terms},
            )

    def _analyze_goals(self) -> None:
        try:
            goals = list(self.orch.goals.goals.values()) if hasattr(self.orch, "goals") else []
        except Exception:
            goals = []
        if not goals:
            return
        failed = [g for g in goals if getattr(g.status, "value", g.status) == "failed"]
        if len(failed) >= 2:
            self._add_rec(
                "general",
                "Multiple goals failed",
                f"{len(failed)} failed goals. Review replan limits and step granularity.",
                confidence=0.7,
                evidence={"failed": len(failed), "total": len(goals)},
            )
        replans = sum(getattr(g, "replan_count", 0) for g in goals)
        if replans >= 3:
            self._add_rec(
                "general",
                "High goal replan frequency",
                f"{replans} total replans. Improve initial decomposition.",
                confidence=0.65,
                evidence={"replans": replans},
            )

    # ── ranking helpers (read-only influence) ─────────────────────

    def planner_agent_bias(self) -> Dict[str, float]:
        """
        Soft bias scores for planner (not applied automatically).
        Positive = prefer, negative = avoid.
        """
        bias: Dict[str, float] = {}
        for agent, st in self.routing_stats.items():
            n = st["n"]
            if n < 2:
                continue
            rate = st["success"] / n
            bias[agent] = round((rate - 0.5) * 2, 3)  # -1..1
        return bias

    def retrieval_term_boost(self, query: str) -> float:
        """Extra score from historical usefulness of query terms."""
        boost = 0.0
        for term in (query or "").lower().split():
            boost += self.retrieval_feedback.get(term, 0.0) * 0.05
        return max(-0.5, min(0.5, boost))

    def suggested_collab_mode(self) -> Optional[str]:
        best, best_rate = None, -1.0
        for mode, st in self.collab_stats.items():
            if st["n"] < 2:
                continue
            rate = st["success"] / st["n"]
            if rate > best_rate:
                best, best_rate = mode, rate
        return best if best_rate >= 0.6 else None

    # ── apply / rollback (explicit only) ───────────────────────────

    def apply_recommendation(self, rec_id: str) -> Dict[str, Any]:
        """Mark recommendation applied — does not mutate core policies silently."""
        for r in self.recommendations:
            if r.id == rec_id:
                r.applied = True
                self.history.append({"type": "apply", "id": rec_id, "ts": time.time()})
                self._save()
                return {"ok": True, "recommendation": r.to_dict(), "note": "Marked applied; review evidence before hard-coding."}
        return {"ok": False, "error": "not found"}

    def rollback_recommendation(self, rec_id: str) -> Dict[str, Any]:
        for r in self.recommendations:
            if r.id == rec_id:
                if not r.reversible:
                    return {"ok": False, "error": "not reversible"}
                r.applied = False
                self.history.append({"type": "rollback", "id": rec_id, "ts": time.time()})
                self._save()
                return {"ok": True, "recommendation": r.to_dict()}
        return {"ok": False, "error": "not found"}

    # ── reports ───────────────────────────────────────────────────

    def report(self) -> str:
        lines = [
            "# Learning report",
            f"Recommendations: {len(self.recommendations)} "
            f"(applied={sum(1 for r in self.recommendations if r.applied)})",
            "",
            "## Routing stats",
        ]
        for agent, st in sorted(self.routing_stats.items()):
            n = st["n"] or 1
            lines.append(
                f"- {agent}: n={st['n']} success={st['success']/n:.0%} "
                f"avg_lat={st['latency_ms']/n:.0f}ms"
            )
        if not self.routing_stats:
            lines.append("- (none yet)")
        lines.append("")
        lines.append("## Collaboration stats")
        for mode, st in sorted(self.collab_stats.items()):
            n = st["n"] or 1
            lines.append(
                f"- {mode}: n={st['n']} success={st['success']/n:.0%} "
                f"avg_dis={st['disagreement']/n:.2f}"
            )
        if not self.collab_stats:
            lines.append("- (none yet)")
        lines.append("")
        lines.append("## Top recommendations")
        open_recs = [r for r in self.recommendations if not r.applied]
        open_recs.sort(key=lambda r: -r.confidence)
        for r in open_recs[:10]:
            lines.append(f"- [{r.category}] ({r.confidence:.2f}) {r.title}")
        if not open_recs:
            lines.append("- (none)")
        return "\n".join(lines)

    def list_recommendations(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        items = self.recommendations
        if category:
            items = [r for r in items if r.category == category]
        return [r.to_dict() for r in sorted(items, key=lambda r: -r.confidence)]

    def optimization_history(self, limit: int = 30) -> List[Dict[str, Any]]:
        return self.history[-limit:]

    def status(self) -> Dict[str, Any]:
        return {
            "recommendations": len(self.recommendations),
            "applied": sum(1 for r in self.recommendations if r.applied),
            "routing_agents": len(self.routing_stats),
            "collab_modes": len(self.collab_stats),
            "workflow_steps": len(self.workflow_step_stats),
            "retrieval_terms": len(self.retrieval_feedback),
            "suggested_collab_mode": self.suggested_collab_mode(),
            "planner_bias": self.planner_agent_bias(),
        }
