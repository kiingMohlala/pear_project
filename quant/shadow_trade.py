"""Immutable shadow trade records — never real fills."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional


TRADE_KIND = "shadow"  # distinguishable from paper / real


@dataclass
class ShadowSignal:
    id: str
    trial_id: str
    strategy_fingerprint: str
    symbol: str
    side: str
    bar_ts: float
    server_ts: float
    price_observed: float
    regime: str = "unknown"
    kind: str = TRADE_KIND
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def create(cls, **kwargs) -> "ShadowSignal":
        return cls(
            id=kwargs.get("id") or f"ssig_{uuid.uuid4().hex[:10]}",
            trial_id=kwargs["trial_id"],
            strategy_fingerprint=kwargs["strategy_fingerprint"],
            symbol=kwargs["symbol"],
            side=kwargs["side"],
            bar_ts=float(kwargs["bar_ts"]),
            server_ts=float(kwargs.get("server_ts") or time.time()),
            price_observed=float(kwargs["price_observed"]),
            regime=kwargs.get("regime") or "unknown",
            meta=dict(kwargs.get("meta") or {}),
        )


@dataclass
class ShadowTrade:
    id: str
    trial_id: str
    strategy_fingerprint: str
    symbol: str
    side: str  # buy=enter long, sell=exit long / etc
    entry_ts: float
    entry_bar_ts: float
    entry_observed: float
    entry_fill_sim: float
    exit_ts: Optional[float] = None
    exit_bar_ts: Optional[float] = None
    exit_observed: Optional[float] = None
    exit_fill_sim: Optional[float] = None
    pnl: Optional[float] = None
    regime_entry: str = "unknown"
    regime_exit: str = "unknown"
    spread_bps: float = 0.0
    slippage_bps: float = 0.0
    latency_bars: int = 0
    kind: str = TRADE_KIND
    server_ts_open: float = field(default_factory=time.time)
    server_ts_close: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    def close(
        self,
        *,
        exit_bar_ts: float,
        exit_observed: float,
        exit_fill_sim: float,
        regime: str,
        position_sign: int,
    ) -> None:
        self.exit_ts = time.time()
        self.exit_bar_ts = exit_bar_ts
        self.exit_observed = exit_observed
        self.exit_fill_sim = exit_fill_sim
        self.regime_exit = regime
        self.server_ts_close = time.time()
        self.pnl = position_sign * (exit_fill_sim - self.entry_fill_sim) / self.entry_fill_sim
