"""Research decision engine — promote/continue/retest/falsify/retire/insufficient."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

from .scorecard import CandidateScorecard, rank_scorecards


class ResearchDecisionType(str, Enum):
    PROMOTE_LONG_HORIZON = "PROMOTE_TO_LONG_HORIZON_VALIDATION"
    CONTINUE_OBSERVATION = "CONTINUE_OBSERVATION"
    RETEST = "RETEST"
    FALSIFIED = "FALSIFIED"
    RETIRE = "RETIRE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass
class ResearchDecision:
    decision: ResearchDecisionType
    candidate_id: str
    hypothesis_id: str
    scorecard: Dict[str, Any]
    rationale: List[str] = field(default_factory=list)
    reproducible: bool = True

    def to_dict(self) -> dict:
        d = asdict(self)
        d["decision"] = self.decision.value
        return d


def decide(card: CandidateScorecard) -> ResearchDecision:
    card.compute_composite()
    reasons: List[str] = []

    if card.evidence_count < 2 or card.trade_count < 5 or card.confidence == "low" and card.markets_tested < 1:
        if card.trade_count < 5 or card.evidence_count < 2:
            reasons.append("sample size below minimum for a strong decision")
            return ResearchDecision(
                ResearchDecisionType.INSUFFICIENT_EVIDENCE,
                card.candidate_id,
                card.hypothesis_id,
                card.to_dict(),
                reasons,
            )

    if "falsified" in [f.lower() for f in card.failure_modes] or card.oos_sharpe < -0.5:
        reasons.append("explicit falsification or severely negative OOS Sharpe")
        return ResearchDecision(
            ResearchDecisionType.FALSIFIED,
            card.candidate_id,
            card.hypothesis_id,
            card.to_dict(),
            reasons,
        )

    if card.backtest_paper_divergence == "HIGH" or card.paper_shadow_divergence == "HIGH":
        reasons.append("HIGH divergence between research and validation layers")
        return ResearchDecision(
            ResearchDecisionType.RETIRE,
            card.candidate_id,
            card.hypothesis_id,
            card.to_dict(),
            reasons,
        )

    if card.max_drawdown > 0.35:
        reasons.append("drawdown exceeds risk tolerance")
        return ResearchDecision(
            ResearchDecisionType.RETIRE,
            card.candidate_id,
            card.hypothesis_id,
            card.to_dict(),
            reasons,
        )

    if (
        card.confidence in ("moderate", "high")
        and card.oos_sharpe >= 0.15
        and card.max_drawdown <= 0.25
        and card.market_robustness >= 0.4
    ):
        reasons.append("multi-dimensional robustness acceptable; promote to long-horizon validation only")
        return ResearchDecision(
            ResearchDecisionType.PROMOTE_LONG_HORIZON,
            card.candidate_id,
            card.hypothesis_id,
            card.to_dict(),
            reasons,
        )

    if card.oos_sharpe >= 0 and card.confidence != "low":
        reasons.append("promising but incomplete evidence — continue observation")
        return ResearchDecision(
            ResearchDecisionType.CONTINUE_OBSERVATION,
            card.candidate_id,
            card.hypothesis_id,
            card.to_dict(),
            reasons,
        )

    reasons.append("borderline metrics — retest with expanded samples")
    return ResearchDecision(
        ResearchDecisionType.RETEST,
        card.candidate_id,
        card.hypothesis_id,
        card.to_dict(),
        reasons,
    )


def compare_hypotheses(cards: List[CandidateScorecard]) -> Dict[str, Any]:
    """Which hypothesis produced the most robust improvement?"""
    ranked = rank_scorecards(cards)
    return {
        "ranking": [
            {
                "hypothesis_id": c.hypothesis_id,
                "candidate_id": c.candidate_id,
                "composite_score": c.composite_score,
                "confidence": c.confidence,
                "oos_sharpe": c.oos_sharpe,
                "total_return": c.total_return,
            }
            for c in ranked
        ],
        "winner_hypothesis": ranked[0].hypothesis_id if ranked else None,
        "criterion": "composite robustness (not total return)",
        "disclaimer": "Historical robustness ranking only — not a forecast of future P&L.",
    }
