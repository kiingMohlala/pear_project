"""
Research Review Board (v0.8) — independent evaluation, scorecards, decisions, lineage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .independent_review import IndependentValidator, series_fingerprint, assert_disjoint
from .scorecard import CandidateScorecard, rank_scorecards
from .research_decision import decide, compare_hypotheses, ResearchDecision
from .dsl import Strategy
from .data import Series
from .trial import fingerprint_strategy
from .hypothesis_engine import HypothesisEngine
from .research_lab import ResearchLab
from .research_memory import ResearchMemory


class ResearchReviewBoard:
    def __init__(
        self,
        memory: Optional[ResearchMemory] = None,
        persist_path: Optional[Path] = None,
    ):
        self.memory = memory or ResearchMemory()
        self.validator = IndependentValidator()
        self.persist_path = Path(persist_path) if persist_path else Path.home() / ".pear" / "quant_review_board.json"
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        self.decisions: List[Dict[str, Any]] = []
        self.scorecards: Dict[str, CandidateScorecard] = {}
        self._load()

    def _load(self) -> None:
        if not self.persist_path.exists():
            return
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
            self.decisions = list(data.get("decisions") or [])
        except Exception:
            pass

    def _save(self) -> None:
        self.persist_path.write_text(
            json.dumps({
                "decisions": self.decisions[-500:],
                "scorecards": {k: v.to_dict() for k, v in self.scorecards.items()},
            }, indent=2),
            encoding="utf-8",
        )

    def independent_evaluate(
        self,
        strategy: Strategy,
        independent_series: Series,
        *,
        research_series: Optional[Series] = None,
        hypothesis_id: str = "",
        evidence_count: int = 0,
        markets_tested: int = 1,
        timeframes_tested: int = 1,
        regimes_tested: int = 1,
        paper_divergence: str = "UNKNOWN",
        shadow_divergence: str = "UNKNOWN",
        parameter_stability: float = 0.5,
        market_robustness: float = 0.5,
        timeframe_robustness: float = 0.5,
        regime_robustness: float = 0.5,
        paper_days: float = 0.0,
        shadow_days: float = 0.0,
        failure_modes: Optional[List[str]] = None,
    ) -> CandidateScorecard:
        review = self.validator.review(
            strategy,
            independent_series,
            research_series=research_series,
        )
        cid = review.candidate_fingerprint
        m = review.metrics
        card = CandidateScorecard(
            candidate_id=cid,
            hypothesis_id=hypothesis_id,
            oos_sharpe=float(m.get("oos_sharpe") or 0),
            max_drawdown=float(m.get("max_drawdown") or 0),
            profit_factor=float(m.get("profit_factor") or 0),
            expectancy=float(m.get("expectancy") or 0),
            trade_count=int(m.get("trades") or 0),
            total_return=float(m.get("total_return") or 0),
            parameter_stability=parameter_stability,
            market_robustness=market_robustness,
            timeframe_robustness=timeframe_robustness,
            regime_robustness=regime_robustness,
            backtest_paper_divergence=paper_divergence,
            paper_shadow_divergence=shadow_divergence,
            monte_carlo_p5=float(review.robustness.get("monte_carlo_p5") or 0),
            consistency_over_time=0.5 + min(0.4, float(review.robustness.get("walk_forward_sharpe") or 0) / 5.0),
            evidence_count=evidence_count or (1 if hypothesis_id else 0),
            markets_tested=markets_tested,
            timeframes_tested=timeframes_tested,
            regimes_tested=regimes_tested,
            oos_periods=1,
            paper_duration_days=paper_days,
            shadow_duration_days=shadow_days,
            failure_modes=list(failure_modes or review.robustness.get("reasons") or []),
            known_limitations=["independent review excludes hypothesis search history"],
        )
        if not review.robustness.get("passed"):
            card.failure_modes = list(dict.fromkeys(card.failure_modes + ["independent_robustness_failed"]))
        card.compute_composite()
        self.scorecards[cid] = card
        self._save()
        return card

    def make_decision(self, candidate_id: str) -> ResearchDecision:
        card = self.scorecards[candidate_id]
        dec = decide(card)
        self.decisions.append(dec.to_dict())
        self._save()
        return dec

    def compare(self, candidate_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        cards = list(self.scorecards.values())
        if candidate_ids:
            cards = [c for c in cards if c.candidate_id in candidate_ids]
        return compare_hypotheses(cards)

    def lineage_query(
        self,
        *,
        hypothesis_id: str = "",
        experiment_id: str = "",
        hypothesis_engine: Optional[HypothesisEngine] = None,
    ) -> Dict[str, Any]:
        """Queryable lineage across hypothesis engine + memory."""
        out: Dict[str, Any] = {"nodes": [], "edges": []}
        if hypothesis_engine and hypothesis_id and hypothesis_id in hypothesis_engine.hypotheses:
            h = hypothesis_engine.hypotheses[hypothesis_id]
            out["nodes"].append({"type": "hypothesis", "id": h.id, "status": h.status.value})
            for pid in h.parent_experiments:
                out["nodes"].append({"type": "experiment", "id": pid})
                out["edges"].append({"from": pid, "to": h.id, "rel": "informed"})
            if h.child_candidate_id:
                out["nodes"].append({"type": "candidate", "id": h.child_candidate_id})
                out["edges"].append({"from": h.id, "to": h.child_candidate_id, "rel": "spawned"})
            for eid in h.child_experiment_ids:
                out["nodes"].append({"type": "experiment", "id": eid})
                out["edges"].append({"from": h.child_candidate_id or h.id, "to": eid, "rel": "tested"})
            out["lineage"] = h.lineage
            out["explanation"] = h.human_readable()
        if experiment_id:
            try:
                exp = self.memory.get(experiment_id)
                out["nodes"].append({
                    "type": "experiment",
                    "id": exp.id,
                    "disposition": exp.disposition.value,
                    "fingerprint": exp.strategy_fingerprint,
                })
            except Exception:
                pass
        return out

    def full_review_package(
        self,
        strategy: Strategy,
        independent_series: Series,
        research_series: Series,
        hypothesis_id: str = "",
        **score_kwargs,
    ) -> Dict[str, Any]:
        card = self.independent_evaluate(
            strategy,
            independent_series,
            research_series=research_series,
            hypothesis_id=hypothesis_id,
            **score_kwargs,
        )
        decision = self.make_decision(card.candidate_id)
        return {
            "scorecard": card.to_dict(),
            "decision": decision.to_dict(),
            "note": "No real-money promotion path exists in this board.",
        }
