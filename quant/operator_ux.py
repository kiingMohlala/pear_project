"""
Operator UX for Quant research (v0.10).

Presentation layer only — no trading, no mutation of frozen candidates.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .research_memory import ResearchMemory
from .hypothesis_engine import HypothesisEngine
from .research_review import ResearchReviewBoard
from .research_decision import ResearchDecisionType
from .experiment import Disposition


def _fmt_decision_explanation(decision: Dict[str, Any], scorecard: Optional[Dict[str, Any]] = None) -> str:
    sc = scorecard or decision.get("scorecard") or {}
    dtype = decision.get("decision") or "UNKNOWN"
    rationale = decision.get("rationale") or []
    lines = [
        f"DECISION: {dtype}",
        "",
        "Evidence",
        f"  experiments/evidence_count: {sc.get('evidence_count', 'n/a')}",
        f"  markets_tested: {sc.get('markets_tested', 'n/a')}",
        f"  timeframes_tested: {sc.get('timeframes_tested', 'n/a')}",
        f"  regimes_tested: {sc.get('regimes_tested', 'n/a')}",
        f"  oos_periods: {sc.get('oos_periods', 'n/a')}",
        f"  paper_duration_days: {sc.get('paper_duration_days', 'n/a')}",
        f"  shadow_duration_days: {sc.get('shadow_duration_days', 'n/a')}",
        f"  trade_count: {sc.get('trade_count', 'n/a')}",
        f"  confidence: {sc.get('confidence', 'n/a')}",
        "",
        "Strengths",
    ]
    strengths = []
    if float(sc.get("max_drawdown") or 1) <= 0.2:
        strengths.append("stable drawdown")
    if float(sc.get("parameter_stability") or 0) >= 0.5:
        strengths.append("acceptable parameter stability")
    if float(sc.get("oos_sharpe") or 0) > 0.2:
        strengths.append("positive OOS Sharpe")
    if sc.get("backtest_paper_divergence") in ("LOW", "UNKNOWN"):
        strengths.append("no HIGH paper divergence")
    if not strengths:
        strengths.append("(none highlighted)")
    for s in strengths:
        lines.append(f"  + {s}")

    lines.append("")
    lines.append("Weaknesses")
    weaknesses = list(sc.get("failure_modes") or [])
    if sc.get("backtest_paper_divergence") == "HIGH":
        weaknesses.append("HIGH backtest→paper divergence")
    if sc.get("paper_shadow_divergence") == "HIGH":
        weaknesses.append("HIGH paper→shadow divergence")
    if float(sc.get("regime_robustness") or 1) < 0.4:
        weaknesses.append("weak regime robustness")
    if not weaknesses:
        weaknesses.append("(none flagged)")
    for w in weaknesses:
        lines.append(f"  - {w}")

    lines.append("")
    lines.append("Reason")
    if rationale:
        for r in rationale:
            lines.append(f"  {r}")
    else:
        lines.append("  See decision type and evidence counts above.")
    lines.append("")
    lines.append("No real-money promotion path. Research/validation only.")
    return "\n".join(lines)


class QuantOperatorUX:
    """Aggregate operator views over research memory, hypotheses, and review board."""

    def __init__(
        self,
        memory: Optional[ResearchMemory] = None,
        hyp_engine: Optional[HypothesisEngine] = None,
        board: Optional[ResearchReviewBoard] = None,
        data_dir: Optional[Path] = None,
    ):
        self.data_dir = Path(data_dir) if data_dir else Path.home() / ".pear" / "quant_ux"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.memory = memory or ResearchMemory(path=self.data_dir / "research_memory.json")
        self.hyp = hyp_engine or HypothesisEngine(
            memory=self.memory,
            persist_path=self.data_dir / "hypotheses.json",
        )
        self.board = board or ResearchReviewBoard(
            memory=self.memory,
            persist_path=self.data_dir / "review_board.json",
        )
        # optional shadow trial ids (read-only paths)
        self.shadow_dir = self.data_dir / "shadow"
        self.shadow_dir.mkdir(parents=True, exist_ok=True)

    def dashboard(self) -> Dict[str, Any]:
        experiments = self.memory.all()
        by_disp: Dict[str, int] = {}
        markets = set()
        timeframes = set()
        for e in experiments:
            by_disp[e.disposition.value] = by_disp.get(e.disposition.value, 0) + 1
            markets.add(e.market)
            timeframes.add(e.timeframe)
        hyps = list(self.hyp.hypotheses.values())
        hyp_by_status: Dict[str, int] = {}
        for h in hyps:
            st = h.status.value if hasattr(h.status, "value") else str(h.status)
            hyp_by_status[st] = hyp_by_status.get(st, 0) + 1
        decisions = list(self.board.decisions)[-10:]
        failures = self.memory.failure_patterns(limit=8)
        last_cycle = max((e.created_at for e in experiments), default=None)
        shadow_active = self._list_shadow_trials()
        return {
            "health": {
                "zero_real_orders": True,
                "allows_capital_allocation": False,
                "memory_experiments": len(experiments),
                "hypotheses": len(hyps),
                "decisions_logged": len(self.board.decisions),
            },
            "candidates_by_stage": by_disp,
            "active_hypotheses": hyp_by_status,
            "active_shadow_trials": shadow_active,
            "markets_observed": sorted(markets),
            "timeframes_observed": sorted(timeframes),
            "recent_research": self.memory.research_history(limit=8),
            "recent_decisions": decisions,
            "failures_detected": failures,
            "last_research_cycle": last_cycle,
            "as_of": time.time(),
        }

    def dashboard_text(self) -> str:
        d = self.dashboard()
        lines = [
            "=== QUANT OPERATOR DASHBOARD ===",
            f"Health: experiments={d['health']['memory_experiments']} "
            f"hypotheses={d['health']['hypotheses']} "
            f"orders={'DISABLED'} capital={'DISABLED'}",
            "",
            "Candidates by lifecycle:",
        ]
        for k, v in sorted((d.get("candidates_by_stage") or {}).items()):
            lines.append(f"  {k}: {v}")
        lines.append("")
        lines.append("Active hypotheses:")
        for k, v in sorted((d.get("active_hypotheses") or {}).items()):
            lines.append(f"  {k}: {v}")
        lines.append("")
        lines.append(f"Markets: {', '.join(d.get('markets_observed') or []) or '—'}")
        lines.append(f"Timeframes: {', '.join(d.get('timeframes_observed') or []) or '—'}")
        lines.append(f"Shadow trials tracked: {len(d.get('active_shadow_trials') or [])}")
        lines.append("")
        lines.append("Recent decisions:")
        for dec in (d.get("recent_decisions") or [])[-5:]:
            lines.append(f"  {dec.get('decision')} · candidate={dec.get('candidate_id', '')[:12]}")
        lines.append("")
        lines.append("Failures:")
        for f in (d.get("failures_detected") or [])[:5]:
            lines.append(f"  {f.get('reason')} (n={f.get('count')})")
        return "\n".join(lines)

    def candidate_view(self, experiment_id: str) -> Dict[str, Any]:
        exp = self.memory.get(experiment_id)
        # find hypothesis that lists this experiment
        origin_h = None
        for h in self.hyp.hypotheses.values():
            if experiment_id in h.child_experiment_ids or experiment_id in h.parent_experiments:
                origin_h = h
                break
        card = None
        decision = None
        fp = exp.strategy_fingerprint
        if fp in self.board.scorecards:
            card = self.board.scorecards[fp].to_dict()
        for dec in reversed(self.board.decisions):
            if dec.get("candidate_id") == fp:
                decision = dec
                break
        return {
            "candidate": {
                "experiment_id": exp.id,
                "fingerprint": exp.strategy_fingerprint,
                "name": exp.strategy_name,
                "family": exp.strategy_family,
                "disposition": exp.disposition.value,
                "params": exp.parameters,
            },
            "origin_hypothesis": origin_h.id if origin_h else None,
            "parent_experiments": origin_h.parent_experiments if origin_h else [],
            "markets": [exp.market],
            "timeframes": [exp.timeframe],
            "oos": exp.oos,
            "backtest": exp.backtest,
            "paper": exp.paper,
            "shadow": {"note": "see shadow trial logs if linked", "divergence": exp.divergence},
            "independent_review": card,
            "confidence": (card or {}).get("confidence"),
            "decision": decision,
            "known_failure_modes": exp.failure_reasons,
            "report": None,  # filled by text helper
        }

    def candidate_view_text(self, experiment_id: str) -> str:
        v = self.candidate_view(experiment_id)
        c = v["candidate"]
        lines = [
            f"Candidate {c['experiment_id']}",
            f"  fingerprint: {c['fingerprint']}",
            f"  family/name: {c['family']} / {c['name']}",
            f"  disposition: {c['disposition']}",
            f"  origin hypothesis: {v.get('origin_hypothesis') or '—'}",
            f"  markets: {v.get('markets')}",
            f"  timeframes: {v.get('timeframes')}",
            f"  OOS: {v.get('oos')}",
            f"  paper: {v.get('paper')}",
            f"  divergence: {(v.get('shadow') or {}).get('divergence')}",
            f"  confidence: {v.get('confidence')}",
            f"  decision: {(v.get('decision') or {}).get('decision')}",
            f"  failure modes: {v.get('known_failure_modes')}",
        ]
        if v.get("decision"):
            lines.append("")
            lines.append(_fmt_decision_explanation(v["decision"]))
        return "\n".join(lines)

    def hypotheses_queue(self) -> List[Dict[str, Any]]:
        rows = []
        for h in sorted(self.hyp.hypotheses.values(), key=lambda x: -x.created_at):
            rows.append({
                "id": h.id,
                "status": h.status.value if hasattr(h.status, "value") else h.status,
                "family": h.strategy_family,
                "evidence_experiments": len(h.parent_experiments),
                "markets": h.market_scope,
                "timeframes": h.timeframe_scope,
                "observed_failure": h.observed_failure,
                "proposed_change": h.proposed_change,
                "mutation_type": h.mutation_type,
            })
        return rows

    def hypothesis_view(self, hypothesis_id: str) -> Dict[str, Any]:
        h = self.hyp.hypotheses[hypothesis_id]
        return {
            "id": h.id,
            "status": h.status.value if hasattr(h.status, "value") else h.status,
            "evidence": h.evidence_summary,
            "parent_experiments": h.parent_experiments,
            "markets": h.market_scope,
            "timeframes": h.timeframe_scope,
            "regimes": h.regime_scope,
            "observed_failure": h.observed_failure,
            "reason": h.reason,
            "proposed_change": h.proposed_change,
            "expected_effect": h.expected_effect,
            "falsification": h.falsification_criteria.to_dict(),
            "lineage": h.lineage,
            "explanation": h.human_readable(),
            "child_candidate_id": h.child_candidate_id,
            "child_experiments": h.child_experiment_ids,
        }

    def hypothesis_view_text(self, hypothesis_id: str) -> str:
        return self.hypotheses_queue and self.hyp.hypotheses[hypothesis_id].human_readable()

    def decision_explanation(self, candidate_id: str) -> str:
        for dec in reversed(self.board.decisions):
            if dec.get("candidate_id") == candidate_id:
                return _fmt_decision_explanation(dec)
        if candidate_id in self.board.scorecards:
            # synthesize decision
            from .research_decision import decide
            d = decide(self.board.scorecards[candidate_id])
            return _fmt_decision_explanation(d.to_dict())
        return f"No decision recorded for candidate {candidate_id}"

    def lineage_view(self, hypothesis_id: str = "", experiment_id: str = "") -> Dict[str, Any]:
        return self.board.lineage_query(
            hypothesis_id=hypothesis_id,
            experiment_id=experiment_id,
            hypothesis_engine=self.hyp,
        )

    def lineage_text(self, hypothesis_id: str = "", experiment_id: str = "") -> str:
        data = self.lineage_view(hypothesis_id=hypothesis_id, experiment_id=experiment_id)
        lines = ["Lineage"]
        for n in data.get("nodes") or []:
            lines.append(f"  [{n.get('type')}] {n.get('id')} {n.get('status') or n.get('disposition') or ''}")
        for e in data.get("edges") or []:
            lines.append(f"  {e.get('from')} --{e.get('rel')}→ {e.get('to')}")
        if data.get("explanation"):
            lines.append("")
            lines.append(str(data["explanation"])[:3000])
        return "\n".join(lines) if len(lines) > 1 else "No lineage nodes found."

    def _list_shadow_trials(self) -> List[Dict[str, Any]]:
        rows = []
        # scan persist dirs used by shadow/matrix if present under home quant paths
        roots = [
            self.shadow_dir,
            Path.home() / ".pear" / "quant_shadow",
            Path.home() / ".pear" / "quant_matrix",
        ]
        for root in roots:
            if not root.exists():
                continue
            for p in root.rglob("shadow_*.json"):
                try:
                    import json
                    d = json.loads(p.read_text(encoding="utf-8"))
                    rows.append({
                        "id": d.get("id") or p.stem,
                        "status": d.get("status"),
                        "symbol": d.get("symbol"),
                        "bars": d.get("bar_index"),
                    })
                except Exception:
                    continue
            if len(rows) > 30:
                break
        return rows[:20]
