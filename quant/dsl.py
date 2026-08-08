"""
Strategy DSL — rule-based strategies only.

Example JSON/dict:
{
  "name": "sma_cross",
  "params": {"fast": 10, "slow": 30},
  "entry": {"type": "cross_above", "a": "sma_fast", "b": "sma_slow"},
  "exit": {"type": "cross_below", "a": "sma_fast", "b": "sma_slow"},
  "side": "long"
}
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


ALLOWED_ENTRY = {"cross_above", "cross_below", "above", "below", "always"}
ALLOWED_EXIT = {"cross_above", "cross_below", "above", "below", "opposite", "hold"}


@dataclass
class StrategySpec:
    name: str
    params: Dict[str, float] = field(default_factory=dict)
    entry: Dict[str, Any] = field(default_factory=lambda: {"type": "cross_above", "a": "sma_fast", "b": "sma_slow"})
    exit: Dict[str, Any] = field(default_factory=lambda: {"type": "cross_below", "a": "sma_fast", "b": "sma_slow"})
    side: str = "long"  # long | short
    indicators: Dict[str, Any] = field(default_factory=lambda: {
        "sma_fast": {"type": "sma", "period": "fast"},
        "sma_slow": {"type": "sma", "period": "slow"},
    })

    def to_dict(self) -> dict:
        return asdict(self)

    def fingerprint(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


def parse_strategy(data: Dict[str, Any] | str) -> "Strategy":
    if isinstance(data, str):
        data = json.loads(data)
    spec = StrategySpec(
        name=str(data.get("name") or "unnamed"),
        params={k: float(v) for k, v in (data.get("params") or {}).items()},
        entry=dict(data.get("entry") or {}),
        exit=dict(data.get("exit") or {}),
        side=str(data.get("side") or "long"),
        indicators=dict(data.get("indicators") or {
            "sma_fast": {"type": "sma", "period": "fast"},
            "sma_slow": {"type": "sma", "period": "slow"},
        }),
    )
    return Strategy(spec)


class Strategy:
    def __init__(self, spec: StrategySpec):
        self.spec = spec

    @property
    def name(self) -> str:
        return self.spec.name

    def clone(self, **param_overrides) -> "Strategy":
        spec = copy.deepcopy(self.spec)
        spec.params.update({k: float(v) for k, v in param_overrides.items()})
        if "name" not in param_overrides:
            bits = "_".join(f"{k}{int(v)}" for k, v in sorted(spec.params.items()))
            spec.name = f"{spec.name.split('_p')[0]}_p{bits}" if bits else spec.name
        return Strategy(spec)

    def resolve_period(self, key: str, default: int = 10) -> int:
        p = self.spec.params.get(key)
        if p is None:
            return default
        return max(2, int(p))
