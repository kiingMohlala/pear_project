"""Shadow trial — frozen candidate, 30/60/90 checkpoints, integrity retirement."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .trial import fingerprint_strategy, TrialVerdict
from .dsl import Strategy, parse_strategy
from .shadow_trade import ShadowSignal, ShadowTrade


class ShadowTrialStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    CHECKPOINT_30 = "checkpoint_30"
    CHECKPOINT_60 = "checkpoint_60"
    CHECKPOINT_90 = "checkpoint_90"
    COMPLETE = "complete"
    RETIRED = "retired"


@dataclass
class ShadowTrial:
    id: str
    strategy_fingerprint: str
    strategy_spec: Dict[str, Any]
    symbol: str
    timeframe: str
    status: ShadowTrialStatus = ShadowTrialStatus.PENDING
    bars_per_day: float = 1.0
    checkpoint_bars: Tuple[int, int, int] = (30, 60, 90)
    bar_index: int = 0
    started_at: Optional[float] = None
    equity: float = 1.0
    peak: float = 1.0
    position: int = 0
    entry_price: float = 0.0
    open_trade_id: Optional[str] = None
    trades: int = 0
    wins: int = 0
    losses: int = 0
    gross_win: float = 0.0
    gross_loss: float = 0.0
    closes: List[float] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    signals: List[Dict[str, Any]] = field(default_factory=list)
    trades_log: List[Dict[str, Any]] = field(default_factory=list)
    checkpoints: Dict[str, Any] = field(default_factory=dict)
    verdict: str = TrialVerdict.HOLD.value
    notes: List[str] = field(default_factory=list)
    mutated: bool = False
    kind: str = "shadow"

    def locked_strategy(self) -> Strategy:
        if self.mutated:
            raise RuntimeError("shadow trial integrity: strategy mutated")
        return parse_strategy(self.strategy_spec)

    def day_count(self) -> float:
        return self.bar_index / max(self.bars_per_day, 1e-9)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        d["closes"] = self.closes[-20:]
        d["equity_curve"] = self.equity_curve[:: max(1, len(self.equity_curve) // 40)] if self.equity_curve else []
        return d

    @classmethod
    def create(
        cls,
        strategy: Strategy,
        symbol: str,
        *,
        timeframe: str = "1m",
        bars_per_day: float = 1.0,
        checkpoint_days: Tuple[int, int, int] = (30, 60, 90),
    ) -> "ShadowTrial":
        spec = strategy.spec.to_dict()
        c30, c60, c90 = checkpoint_days
        return cls(
            id=f"shadow_{uuid.uuid4().hex[:12]}",
            strategy_fingerprint=fingerprint_strategy(spec),
            strategy_spec=spec,
            symbol=symbol,
            timeframe=timeframe,
            bars_per_day=bars_per_day,
            checkpoint_bars=(int(c30 * bars_per_day), int(c60 * bars_per_day), int(c90 * bars_per_day)),
        )
