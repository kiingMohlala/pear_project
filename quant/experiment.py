"""
Immutable experiment records (Quant v0.4).

An experiment is a sealed snapshot of one research evaluation cycle.
It must not be mutated after finalization.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class Disposition(str, Enum):
    PENDING = "pending"
    SURVIVED = "survived"
    FAILED = "failed"
    RETIRED = "retired"
    PROMOTED = "promoted"
    CONTINUED = "continued"


def _fingerprint(obj: dict) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


@dataclass
class ExperimentRecord:
    id: str
    strategy_fingerprint: str
    strategy_family: str
    strategy_name: str
    strategy_spec: Dict[str, Any]
    market: str
    timeframe: str
    dataset_id: str  # e.g. hash of series length/seed/source
    parameters: Dict[str, Any]
    backtest: Dict[str, Any] = field(default_factory=dict)
    oos: Dict[str, Any] = field(default_factory=dict)
    monte_carlo: Dict[str, Any] = field(default_factory=dict)
    paper: Dict[str, Any] = field(default_factory=dict)
    divergence: Dict[str, Any] = field(default_factory=dict)
    regimes: Dict[str, Any] = field(default_factory=dict)
    execution: Dict[str, Any] = field(default_factory=dict)
    failure_reasons: List[str] = field(default_factory=list)
    disposition: Disposition = Disposition.PENDING
    notes: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    finalized_at: Optional[float] = None
    sealed: bool = False
    content_hash: str = ""

    def seal(self) -> None:
        if self.sealed:
            raise RuntimeError("experiment already sealed — immutable")
        self.finalized_at = time.time()
        self.sealed = True
        self.content_hash = self.compute_hash()

    def compute_hash(self) -> str:
        payload = {
            "id": self.id,
            "strategy_fingerprint": self.strategy_fingerprint,
            "strategy_spec": self.strategy_spec,
            "market": self.market,
            "timeframe": self.timeframe,
            "dataset_id": self.dataset_id,
            "parameters": self.parameters,
            "backtest": self.backtest,
            "oos": self.oos,
            "monte_carlo": self.monte_carlo,
            "paper": self.paper,
            "divergence": self.divergence,
            "disposition": self.disposition.value if isinstance(self.disposition, Disposition) else self.disposition,
        }
        return _fingerprint(payload)

    def verify_integrity(self) -> bool:
        if not self.sealed:
            return True
        return self.content_hash == self.compute_hash()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["disposition"] = self.disposition.value if isinstance(self.disposition, Disposition) else self.disposition
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ExperimentRecord":
        disp = d.get("disposition", "pending")
        if isinstance(disp, str):
            disp = Disposition(disp)
        return cls(
            id=d["id"],
            strategy_fingerprint=d["strategy_fingerprint"],
            strategy_family=d.get("strategy_family") or "unknown",
            strategy_name=d.get("strategy_name") or "",
            strategy_spec=dict(d.get("strategy_spec") or {}),
            market=d.get("market") or "",
            timeframe=d.get("timeframe") or "",
            dataset_id=d.get("dataset_id") or "",
            parameters=dict(d.get("parameters") or {}),
            backtest=dict(d.get("backtest") or {}),
            oos=dict(d.get("oos") or {}),
            monte_carlo=dict(d.get("monte_carlo") or {}),
            paper=dict(d.get("paper") or {}),
            divergence=dict(d.get("divergence") or {}),
            regimes=dict(d.get("regimes") or {}),
            execution=dict(d.get("execution") or {}),
            failure_reasons=list(d.get("failure_reasons") or []),
            disposition=disp,
            notes=list(d.get("notes") or []),
            tags=list(d.get("tags") or []),
            created_at=float(d.get("created_at") or time.time()),
            finalized_at=d.get("finalized_at"),
            sealed=bool(d.get("sealed")),
            content_hash=d.get("content_hash") or "",
        )


def new_experiment(
    strategy_spec: Dict[str, Any],
    *,
    market: str,
    timeframe: str,
    dataset_id: str,
    family: Optional[str] = None,
) -> ExperimentRecord:
    from .trial import fingerprint_strategy
    name = str(strategy_spec.get("name") or "unnamed")
    family = family or name.split("_")[0]
    return ExperimentRecord(
        id=f"exp_{uuid.uuid4().hex[:12]}",
        strategy_fingerprint=fingerprint_strategy(strategy_spec),
        strategy_family=family,
        strategy_name=name,
        strategy_spec=dict(strategy_spec),
        market=market,
        timeframe=timeframe,
        dataset_id=dataset_id,
        parameters=dict(strategy_spec.get("params") or {}),
    )
