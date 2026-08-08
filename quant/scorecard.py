"""Multi-dimensional candidate scorecard — not ranked by total return."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class CandidateScorecard:
    candidate_id: str
    hypothesis_id: str = ""
    # core metrics
    oos_sharpe: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    trade_count: int = 0
    total_return: float = 0.0  # recorded but weakly weighted
    # robustness dimensions
    parameter_stability: float = 0.5  # 0..1
    market_robustness: float = 0.5
    timeframe_robustness: float = 0.5
    regime_robustness: float = 0.5
    backtest_paper_divergence: str = "UNKNOWN"
    paper_shadow_divergence: str = "UNKNOWN"
    monte_carlo_p5: float = 0.0
    consistency_over_time: float = 0.5
    # evidence
    evidence_count: int = 0
    markets_tested: int = 0
    timeframes_tested: int = 0
    regimes_tested: int = 0
    oos_periods: int = 0
    paper_duration_days: float = 0.0
    shadow_duration_days: float = 0.0
    failure_modes: List[str] = field(default_factory=list)
    known_limitations: List[str] = field(default_factory=list)
    confidence: str = "low"  # low | moderate | high
    composite_score: float = 0.0

    def compute_composite(self) -> float:
        div_pen = 0.0
        if self.backtest_paper_divergence == "HIGH" or self.paper_shadow_divergence == "HIGH":
            div_pen = 0.35
        elif self.backtest_paper_divergence == "MEDIUM" or self.paper_shadow_divergence == "MEDIUM":
            div_pen = 0.12
        score = 0.0
        score += 0.20 * max(-2.0, min(3.0, self.oos_sharpe)) / 3.0
        score += 0.15 * max(0.0, 1.0 - self.max_drawdown)
        score += 0.10 * min(2.0, self.profit_factor) / 2.0
        score += 0.08 * max(-1.0, min(1.0, self.expectancy * 10))
        score += 0.07 * min(1.0, self.trade_count / 25.0)
        score += 0.08 * self.parameter_stability
        score += 0.08 * self.market_robustness
        score += 0.06 * self.timeframe_robustness
        score += 0.06 * self.regime_robustness
        score += 0.07 * self.consistency_over_time
        score += 0.05 * max(-1.0, min(1.0, self.monte_carlo_p5))
        # total_return weak
        score += 0.03 * max(-1.0, min(1.0, self.total_return))
        score -= div_pen
        # sample-size confidence dampening
        if self.evidence_count < 3 or self.markets_tested < 2:
            score *= 0.85
        if self.trade_count < 5:
            score *= 0.7
        self.composite_score = score
        self.confidence = self._confidence()
        return score

    def _confidence(self) -> str:
        if self.trade_count < 5 or self.evidence_count < 2:
            return "low"
        if self.backtest_paper_divergence == "HIGH" or self.paper_shadow_divergence == "HIGH":
            return "low"
        if (
            self.evidence_count >= 5
            and self.markets_tested >= 2
            and self.timeframes_tested >= 2
            and self.trade_count >= 15
            and self.oos_sharpe > 0.2
        ):
            return "high"
        if self.evidence_count >= 3 and self.trade_count >= 8:
            return "moderate"
        return "low"

    def to_dict(self) -> dict:
        self.compute_composite()
        return asdict(self)


def rank_scorecards(cards: List[CandidateScorecard]) -> List[CandidateScorecard]:
    for c in cards:
        c.compute_composite()
    return sorted(cards, key=lambda c: -c.composite_score)
