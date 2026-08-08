"""Realistic execution assumptions for paper validation (not live trading)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExecutionModel:
    """
    Costs applied on every virtual fill.
    Spread is already partially in broker quotes; this adds commission + slippage + delay.
    """
    spread_bps: float = 1.0          # extra half-spread impact if mid-priced
    commission_bps: float = 1.0      # round-trip commission allocated per side
    slippage_bps: float = 0.5        # adverse move on fill
    delay_bars: int = 0              # signal on bar i → fill on i+delay (anti look-ahead)

    def apply_fill_price(self, mid: float, side: str) -> float:
        half_spread = mid * (self.spread_bps / 10000.0) / 2.0
        slip = mid * (self.slippage_bps / 10000.0)
        commission = mid * (self.commission_bps / 10000.0)
        if side == "buy":
            return mid + half_spread + slip + commission
        return mid - half_spread - slip - commission

    def to_dict(self) -> dict:
        return {
            "spread_bps": self.spread_bps,
            "commission_bps": self.commission_bps,
            "slippage_bps": self.slippage_bps,
            "delay_bars": self.delay_bars,
        }
