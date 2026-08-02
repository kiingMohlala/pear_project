"""
Multi-agent collaboration & consensus (v1.90).

Modes: sequential, parallel, reviewer, consensus.
Agents never call each other directly — CollaborationManager routes via Orchestrator.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .orchestrator import Orchestrator


class CollabMode(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    REVIEWER = "reviewer"
    CONSENSUS = "consensus"


@dataclass
class ReviewResult:
    score: float
    feedback: str = ""
    issues: List[str] = field(default_factory=list)
    raw: str = ""
    reviewer: str = "heuristic"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AgentContribution:
    agent: str
    reply: str
    ok: bool
    confidence: float = 0.5
    review: Optional[Dict[str, Any]] = None
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CollabResult:
    id: str
    mode: str
    objective: str
    ok: bool
    reply: str
    contributions: List[AgentContribution] = field(default_factory=list)
    rounds: int = 1
    disagreement: float = 0.0
    final_confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "mode": self.mode,
            "objective": self.objective,
            "ok": self.ok,
            "reply": self.reply,
            "contributions": [c.to_dict() for c in self.contributions],
            "rounds": self.rounds,
            "disagreement": self.disagreement,
            "final_confidence": self.final_confidence,
            "metadata": self.metadata,
        }


def parse_review(text: str, answer: str = "") -> ReviewResult:
    score = 0.6
    feedback = ""
    issues: List[str] = []
    body = text or ""
    m = re.search(r"SCORE:\s*([0-9]*\.?[0-9]+)", body, re.I)
    if m:
        try:
            score = float(m.group(1))
            if score > 1.0:
                score = score / 100.0
        except ValueError:
            pass
    m = re.search(r"FEEDBACK:\s*(.+?)(?:\nISSUES:|$)", body, re.S | re.I)
    if m:
        feedback = m.group(1).strip()
    m = re.search(r"ISSUES:\s*(.+)$", body, re.S | re.I)
    if m:
        issues = [x.strip() for x in re.split(r"[,;\n]", m.group(1)) if x.strip() and x.strip().lower() != "none"]
    if not feedback:
        feedback = body[:300] if body else "No detailed feedback."
    score = max(0.0, min(1.0, score))
    return ReviewResult(score=score, feedback=feedback, issues=issues, raw=body)


def heuristic_review(objective: str, answer: str) -> ReviewResult:
    """Deterministic review for offline/EchoLLM mode."""
    ans = (answer or "").strip()
    obj = (objective or "").strip()
    issues: List[str] = []
    score = 0.55
    if not ans:
        return ReviewResult(score=0.1, feedback="Empty answer.", issues=["empty"])
    if len(ans) < 20:
        issues.append("too short")
        score -= 0.15
    else:
        score += 0.1
    # keyword overlap with objective
    obj_toks = {t for t in re.split(r"\W+", obj.lower()) if len(t) > 3}
    ans_toks = {t for t in re.split(r"\W+", ans.lower()) if len(t) > 3}
    if obj_toks:
        overlap = len(obj_toks & ans_toks) / len(obj_toks)
        score += 0.25 * overlap
        if overlap < 0.15:
            issues.append("low relevance to objective")
    if "error" in ans.lower() or "failed" in ans.lower():
        issues.append("reports failure")
        score -= 0.2
    if len(ans) > 80:
        score += 0.05
    score = max(0.0, min(1.0, score))
    feedback = "Heuristic review based on length and relevance."
    if issues:
        feedback += " Issues: " + ", ".join(issues)
    return ReviewResult(score=round(score, 3), feedback=feedback, issues=issues, reviewer="heuristic")


def estimate_confidence(reply: str, ok: bool) -> float:
    if not ok:
        return 0.15
    text = (reply or "").strip()
    if not text:
        return 0.1
    conf = 0.45
    if len(text) > 40:
        conf += 0.15
    if len(text) > 120:
        conf += 0.1
    if any(w in text.lower() for w in ("uncertain", "not sure", "unknown", "failed")):
        conf -= 0.2
    if any(w in text.lower() for w in ("according to", "sources", "because", "therefore")):
        conf += 0.1
    return max(0.0, min(1.0, conf))


def disagreement_score(replies: List[str]) -> float:
    """0 = identical, 1 = fully divergent (token Jaccard distance)."""
    if len(replies) < 2:
        return 0.0
    tokenized = [{t for t in re.split(r"\W+", (r or "").lower()) if len(t) > 2} for r in replies]
    distances = []
    for i in range(len(tokenized)):
        for j in range(i + 1, len(tokenized)):
            a, b = tokenized[i], tokenized[j]
            if not a and not b:
                distances.append(0.0)
            elif not a or not b:
                distances.append(1.0)
            else:
                distances.append(1.0 - len(a & b) / len(a | b))
    return round(sum(distances) / len(distances), 4) if distances else 0.0


class CollaborationManager:
    def __init__(
        self,
        orchestrator: "Orchestrator",
        *,
        min_review_score: float = 0.55,
        max_rounds: int = 2,
    ):
        self.orch = orchestrator
        self.min_review_score = min_review_score
        self.max_rounds = max_rounds
        self.history: List[CollabResult] = []

    def _span(self, name: str, **attrs):
        try:
            from .tracing import get_tracer
            return get_tracer().span(name, kind="collab", **attrs)
        except Exception:
            from contextlib import nullcontext
            return nullcontext()

    def _emit(self, kind: str, **payload):
        try:
            from .events import EventType
            self.orch.events.emit(
                EventType.NOTE,
                {"kind": kind, **payload},
                source="collaboration",
            )
        except Exception:
            pass

    def choose_mode(self, objective: str, agents: Optional[List[str]] = None) -> CollabMode:
        obj = (objective or "").lower()
        if any(k in obj for k in ("consensus", "agree", "vote")):
            return CollabMode.CONSENSUS
        if any(k in obj for k in ("review", "critique", "quality")):
            return CollabMode.REVIEWER
        if any(k in obj for k in ("parallel", "simultaneously", "multiple agents")):
            return CollabMode.PARALLEL
        # complexity heuristic
        complexity = 0
        if len(obj) > 120:
            complexity += 1
        if any(k in obj for k in ("and", "then", "compare", "analyze", "research")):
            complexity += 1
        if agents and len(agents) >= 3:
            complexity += 1
        if complexity >= 2:
            return CollabMode.CONSENSUS
        if complexity == 1:
            return CollabMode.REVIEWER
        return CollabMode.SEQUENTIAL

    def run(
        self,
        objective: str,
        *,
        agents: Optional[List[str]] = None,
        mode: Optional[str] = None,
        min_score: Optional[float] = None,
        max_rounds: Optional[int] = None,
    ) -> CollabResult:
        min_score = self.min_review_score if min_score is None else min_score
        max_rounds = self.max_rounds if max_rounds is None else max_rounds
        agent_names = agents or self._default_agents(objective)
        collab_mode = CollabMode(mode) if mode else self.choose_mode(objective, agent_names)
        self._emit("collab_started", mode=collab_mode.value, agents=agent_names)

        with self._span("collab.run", mode=collab_mode.value):
            if collab_mode == CollabMode.SEQUENTIAL:
                result = self._sequential(objective, agent_names)
            elif collab_mode == CollabMode.PARALLEL:
                result = self._parallel(objective, agent_names)
            elif collab_mode == CollabMode.REVIEWER:
                result = self._reviewer_loop(objective, agent_names, min_score, max_rounds)
            else:
                result = self._consensus(objective, agent_names, min_score, max_rounds)

        self.history.append(result)
        self._emit("collab_finished", id=result.id, ok=result.ok, mode=result.mode)
        return result

    def _default_agents(self, objective: str) -> List[str]:
        # pick up to 2 specialists via planner scoring if available
        names = []
        try:
            agents = getattr(self.orch, "agents", {})
            from .task import Task
            task = Task(objective=objective)
            scored = []
            for name, agent in agents.items():
                if name in ("reviewer", "critic"):
                    continue
                try:
                    scored.append((agent.can_handle(task), name))
                except Exception:
                    scored.append((0.0, name))
            scored.sort(reverse=True)
            names = [n for s, n in scored[:2] if s > 0.2]
        except Exception:
            pass
        if not names:
            names = ["personal"]
        return names

    def _run_agent(self, name: str, objective: str) -> AgentContribution:
        t0 = time.time()
        try:
            # Prefer direct agent.think to avoid recursive collaboration
            agent = self.orch.agents.get(name)
            if agent is None:
                r = self.orch.route(objective)
                reply = r.get("reply") or ""
                ok = bool(r.get("ok"))
                agent_name = r.get("agent") or name
            else:
                r = agent.think(objective)
                reply = r.get("reply") or r.get("error") or ""
                ok = bool(r.get("ok", True))
                agent_name = name
        except Exception as e:
            reply, ok, agent_name = str(e), False, name
        ms = (time.time() - t0) * 1000
        return AgentContribution(
            agent=agent_name,
            reply=str(reply),
            ok=ok,
            confidence=estimate_confidence(str(reply), ok),
            latency_ms=ms,
        )

    def _review(self, objective: str, answer: str) -> ReviewResult:
        with self._span("collab.review"):
            reviewer = self.orch.agents.get("reviewer") or self.orch.agents.get("critic")
            if reviewer is not None:
                try:
                    r = reviewer.think(
                        f"Objective: {objective}\n\nAnswer:\n{answer}"
                    )
                    if r.get("review"):
                        d = r["review"]
                        return ReviewResult(
                            score=float(d.get("score", 0.5)),
                            feedback=str(d.get("feedback") or ""),
                            issues=list(d.get("issues") or []),
                            reviewer=reviewer.name,
                        )
                    return parse_review(r.get("reply") or "", answer)
                except Exception:
                    pass
            return heuristic_review(objective, answer)

    def _sequential(self, objective: str, agents: List[str]) -> CollabResult:
        with self._span("collab.sequential"):
            contribs = []
            context = objective
            for name in agents:
                c = self._run_agent(name, context)
                contribs.append(c)
                context = (
                    f"{objective}\n\nPrevious ({c.agent}):\n{c.reply}\n\n"
                    f"Continue or refine the answer."
                )
            final = contribs[-1] if contribs else AgentContribution("none", "", False)
            return CollabResult(
                id=f"col_{uuid.uuid4().hex[:10]}",
                mode=CollabMode.SEQUENTIAL.value,
                objective=objective,
                ok=final.ok,
                reply=final.reply,
                contributions=contribs,
                final_confidence=final.confidence,
            )

    def _parallel(self, objective: str, agents: List[str]) -> CollabResult:
        with self._span("collab.parallel"):
            # sequential execution of "parallel" branches (no threads required)
            contribs = [self._run_agent(name, objective) for name in agents]
            replies = [c.reply for c in contribs if c.ok]
            dis = disagreement_score(replies)
            # pick highest confidence
            best = max(contribs, key=lambda c: c.confidence) if contribs else None
            reply = best.reply if best else ""
            if len(replies) > 1:
                reply = self._merge_replies(objective, contribs)
            return CollabResult(
                id=f"col_{uuid.uuid4().hex[:10]}",
                mode=CollabMode.PARALLEL.value,
                objective=objective,
                ok=any(c.ok for c in contribs),
                reply=reply,
                contributions=contribs,
                disagreement=dis,
                final_confidence=best.confidence if best else 0.0,
            )

    def _reviewer_loop(
        self,
        objective: str,
        agents: List[str],
        min_score: float,
        max_rounds: int,
    ) -> CollabResult:
        with self._span("collab.reviewer_loop"):
            primary = agents[0] if agents else "personal"
            contribs: List[AgentContribution] = []
            answer = ""
            review = ReviewResult(score=0.0, feedback="no attempt")
            for round_i in range(1, max_rounds + 1):
                prompt = objective if round_i == 1 else (
                    f"{objective}\n\nRevise your answer based on this feedback:\n"
                    f"{review.feedback}\nIssues: {', '.join(review.issues) or 'none'}\n"
                    f"Previous answer:\n{answer}"
                )
                c = self._run_agent(primary, prompt)
                answer = c.reply
                review = self._review(objective, answer)
                c.review = review.to_dict()
                c.confidence = max(c.confidence, review.score)
                contribs.append(c)
                self._emit("collab_review_round", round=round_i, score=review.score)
                if review.score >= min_score:
                    break
            return CollabResult(
                id=f"col_{uuid.uuid4().hex[:10]}",
                mode=CollabMode.REVIEWER.value,
                objective=objective,
                ok=bool(answer) and review.score >= min_score * 0.8,
                reply=answer,
                contributions=contribs,
                rounds=len(contribs),
                final_confidence=review.score,
                metadata={"final_review": review.to_dict(), "min_score": min_score},
            )

    def _consensus(
        self,
        objective: str,
        agents: List[str],
        min_score: float,
        max_rounds: int,
    ) -> CollabResult:
        with self._span("collab.consensus"):
            # parallel opinions
            base = self._parallel(objective, agents)
            dis = base.disagreement
            reply = base.reply
            rounds = 1
            contribs = list(base.contributions)
            # if high disagreement, review-merge
            if dis > 0.45 and max_rounds > 1:
                review = self._review(objective, reply)
                if review.score < min_score:
                    refine = self._run_agent(
                        agents[0],
                        f"{objective}\n\nSynthesize a consensus answer from:\n"
                        + "\n---\n".join(f"{c.agent}: {c.reply}" for c in contribs)
                        + f"\n\nReviewer feedback: {review.feedback}",
                    )
                    contribs.append(refine)
                    reply = refine.reply
                    rounds = 2
                    review = self._review(objective, reply)
                final_conf = review.score
            else:
                final_conf = base.final_confidence
            return CollabResult(
                id=f"col_{uuid.uuid4().hex[:10]}",
                mode=CollabMode.CONSENSUS.value,
                objective=objective,
                ok=bool(reply),
                reply=reply,
                contributions=contribs,
                rounds=rounds,
                disagreement=dis,
                final_confidence=final_conf,
            )

    def _merge_replies(self, objective: str, contribs: List[AgentContribution]) -> str:
        lines = [f"Consensus draft for: {objective}\n"]
        for c in contribs:
            if c.ok and c.reply:
                lines.append(f"- ({c.agent}, conf={c.confidence:.2f}) {c.reply[:400]}")
        return "\n".join(lines)

    def review_text(self, objective: str, answer: str) -> ReviewResult:
        return self._review(objective, answer)
