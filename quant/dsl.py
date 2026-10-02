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
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


ALLOWED_ENTRY = {"cross_above", "cross_below", "above", "below", "always"}
ALLOWED_EXIT = {"cross_above", "cross_below", "above", "below", "opposite", "hold"}

# PEAR 3.2 Task 018: quant_research/quant_review accept `name`/`family`
# and (validated at its own call site in
# core/connectors/quant_connector.py) `symbol`/`market` as free text
# with no validation anywhere. QuantConnector's research corpus
# (research_memory.json, hypotheses.json, review_board.json) is
# genuinely shared across every user by design -- ExperimentRecord and
# Hypothesis have no user-identity field at all, and that's intentional
# (the corpus's whole value is aggregate, cross-session research
# results).
#
# What this validator actually does, stated honestly rather than
# overclaimed: it rejects the *worst* cases outright -- embedded NUL
# bytes, multi-word sentences (spaces are allowed for names like "S&P
# 500" but a long free-text message reads very differently from a
# short identifier), excessive length, and non-identifier punctuation.
# It does NOT, and structurally cannot, distinguish a real market
# symbol from a short, identifier-shaped secret someone chooses to
# submit instead: "ALICE_PRIVATE_PORTFOLIO_XYZ" is syntactically
# indistinguishable from "BTCUSDT" or "sma_cross_v2" -- any charset
# permissive enough to accept legitimate tickers and strategy names is
# also permissive enough to accept a short underscore-separated label
# someone chose to use as a message instead. Verified directly: the
# exact string from the original Re-Audit 3 reproduction still passes
# this validator unchanged. The max length here (24, tightened from an
# initial, too-generous 40 during this task after that reproduction
# showed 40 chars is plenty of "bandwidth" for a short private note)
# reduces how much can be smuggled this way, it does not eliminate it.
# Closing that residual gap is a disclosure decision, not a validation
# one -- see the docstring on QuantConnector below and this task's
# final report for the reasoning.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._\-/ ]{0,22}[A-Za-z0-9])?$")



def validate_shared_identifier(value: Any, field_name: str, max_len: int = 24) -> str:
    v = str(value if value is not None else "").strip()
    if not v:
        raise ValueError(f"{field_name} must not be empty")
    if len(v) > max_len:
        raise ValueError(f"{field_name} exceeds {max_len} characters")
    if not _IDENTIFIER_RE.match(v):
        raise ValueError(
            f"{field_name} must look like a market/strategy identifier "
            f"(letters, digits, '.', '_', '-', '/', spaces only) -- got {v!r}"
        )
    return v


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
