"""Strategy lifecycle: Candidate → Paper → Pilot → Production → Retired."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class Stage(str, Enum):
    CANDIDATE = "candidate"
    PAPER = "paper"
    PILOT = "pilot"
    PRODUCTION = "production"
    RETIRED = "retired"


STAGE_ORDER = [Stage.CANDIDATE, Stage.PAPER, Stage.PILOT, Stage.PRODUCTION, Stage.RETIRED]


@dataclass
class PromotionThresholds:
    # Candidate → Paper (from lab robustness)
    min_lab_sharpe: float = 0.2
    max_lab_drawdown: float = 0.35
    # Paper → Pilot
    min_paper_trades: int = 20
    min_paper_days: float = 7.0
    min_paper_sharpe: float = 0.15
    max_paper_drawdown: float = 0.25
    min_profit_factor: float = 1.05
    # Pilot → Production
    min_pilot_trades: int = 50
    min_pilot_days: float = 60.0  # ~2 months
    min_pilot_sharpe: float = 0.25
    max_pilot_drawdown: float = 0.20
    # Demote / retire
    retire_sharpe_below: float = -0.3
    retire_drawdown_above: float = 0.40
    demote_consecutive_weak: int = 3


@dataclass
class PromotionDecision:
    strategy_id: str
    from_stage: str
    to_stage: str
    action: str  # promote | demote | retire | hold
    reasons: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_promotion(
    stage: Stage,
    metrics: Dict[str, Any],
    thresholds: Optional[PromotionThresholds] = None,
) -> PromotionDecision:
    th = thresholds or PromotionThresholds()
    m = metrics or {}
    trades = int(m.get("trades") or 0)
    days = float(m.get("days") or 0)
    sharpe = float(m.get("sharpe") or 0)
    dd = float(m.get("max_drawdown") or 0)
    pf = float(m.get("profit_factor") or 0)
    sid = str(m.get("strategy_id") or "")
    reasons: List[str] = []

    # Hard retire
    if sharpe < th.retire_sharpe_below or dd > th.retire_drawdown_above:
        reasons.append(f"risk breach sharpe={sharpe:.2f} dd={dd:.2%}")
        return PromotionDecision(sid, stage.value, Stage.RETIRED.value, "retire", reasons, m)

    if stage == Stage.CANDIDATE:
        if sharpe >= th.min_lab_sharpe and dd <= th.max_lab_drawdown:
            return PromotionDecision(sid, stage.value, Stage.PAPER.value, "promote", ["lab thresholds met"], m)
        reasons.append("lab thresholds not met")
        return PromotionDecision(sid, stage.value, stage.value, "hold", reasons, m)

    if stage == Stage.PAPER:
        ok = (
            trades >= th.min_paper_trades
            and days >= th.min_paper_days
            and sharpe >= th.min_paper_sharpe
            and dd <= th.max_paper_drawdown
            and pf >= th.min_profit_factor
        )
        if ok:
            return PromotionDecision(sid, stage.value, Stage.PILOT.value, "promote", ["paper validation passed"], m)
        if sharpe < 0 and trades >= th.min_paper_trades:
            return PromotionDecision(sid, stage.value, Stage.RETIRED.value, "retire", ["paper underperformance"], m)
        reasons.append("awaiting more paper evidence")
        return PromotionDecision(sid, stage.value, stage.value, "hold", reasons, m)

    if stage == Stage.PILOT:
        ok = (
            trades >= th.min_pilot_trades
            and days >= th.min_pilot_days
            and sharpe >= th.min_pilot_sharpe
            and dd <= th.max_pilot_drawdown
        )
        if ok:
            return PromotionDecision(sid, stage.value, Stage.PRODUCTION.value, "promote", ["pilot thresholds met"], m)
        if sharpe < th.retire_sharpe_below / 2:
            return PromotionDecision(sid, stage.value, Stage.PAPER.value, "demote", ["pilot weakness"], m)
        return PromotionDecision(sid, stage.value, stage.value, "hold", ["pilot in progress"], m)

    if stage == Stage.PRODUCTION:
        if sharpe < th.retire_sharpe_below or dd > th.retire_drawdown_above:
            return PromotionDecision(sid, stage.value, Stage.RETIRED.value, "retire", ["production risk"], m)
        if sharpe < 0 and dd > th.max_pilot_drawdown:
            return PromotionDecision(sid, stage.value, Stage.PILOT.value, "demote", ["production soft underperformance"], m)
        return PromotionDecision(sid, stage.value, stage.value, "hold", ["production stable"], m)

    return PromotionDecision(sid, stage.value, Stage.RETIRED.value, "hold", ["already retired"], m)
