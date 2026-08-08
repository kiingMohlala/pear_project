"""
Immutable research hypotheses (Quant v0.7).

A hypothesis is a sealed proposal grounded in prior experiments.
It never mutates frozen candidates; it only spawns new research candidates.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class HypothesisStatus(str, Enum):
    PROPOSED = "proposed"
    REJECTED_EVIDENCE = "rejected_insufficient_evidence"
    CANDIDATE_SPAWNED = "candidate_spawned"
    TESTING = "testing"
    FALSIFIED = "falsified"
    SURVIVED = "survived"
    ARCHIVED = "archived"


class MutationType(str, Enum):
    PARAMETER = "parameter_modification"
    INDICATOR_ADD = "indicator_addition"
    INDICATOR_REMOVE = "indicator_removal"
    ENTRY = "entry_condition_modification"
    EXIT = "exit_condition_modification"
    REGIME_FILTER = "regime_filter"
    VOLATILITY_FILTER = "volatility_filter"
    EXECUTION_FILTER = "execution_filter"
    COMBINE = "combine_successful_components"


@dataclass
class FalsificationCriteria:
    max_oos_sharpe_degradation_pct: float = 15.0
    max_drawdown_increase_pct: float = 10.0
    min_trades: int = 5
    require_walk_forward_improvement: bool = True
    min_markets_showing_improvement: int = 2
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FalsificationCriteria":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Hypothesis:
    id: str
    parent_experiments: List[str]
    parent_strategies: List[str]  # fingerprints or names
    reason: str
    observed_failure: str
    proposed_change: str
    expected_effect: str
    falsification_criteria: FalsificationCriteria
    market_scope: List[str] = field(default_factory=list)
    timeframe_scope: List[str] = field(default_factory=list)
    regime_scope: List[str] = field(default_factory=list)
    mutation_type: str = MutationType.PARAMETER.value
    strategy_family: str = ""
    base_strategy_spec: Dict[str, Any] = field(default_factory=dict)
    proposed_strategy_spec: Dict[str, Any] = field(default_factory=dict)
    evidence_summary: Dict[str, Any] = field(default_factory=dict)
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    lineage: List[Dict[str, str]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    model_provenance: str = "quant_hypothesis_engine_v07"
    sealed: bool = False
    content_hash: str = ""
    explanation: str = ""
    child_candidate_id: str = ""
    child_experiment_ids: List[str] = field(default_factory=list)

    def seal(self) -> None:
        if self.sealed:
            raise RuntimeError("hypothesis already sealed")
        self.sealed = True
        self.content_hash = self.compute_hash()

    def compute_hash(self) -> str:
        payload = {
            "id": self.id,
            "parent_experiments": self.parent_experiments,
            "proposed_change": self.proposed_change,
            "proposed_strategy_spec": self.proposed_strategy_spec,
            "falsification": self.falsification_criteria.to_dict(),
            "reason": self.reason,
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:20]

    def verify(self) -> bool:
        if not self.sealed:
            return True
        return self.content_hash == self.compute_hash()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value if isinstance(self.status, HypothesisStatus) else self.status
        d["falsification_criteria"] = self.falsification_criteria.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Hypothesis":
        fc = FalsificationCriteria.from_dict(d.get("falsification_criteria") or {})
        st = d.get("status", "proposed")
        if isinstance(st, str):
            st = HypothesisStatus(st)
        return cls(
            id=d["id"],
            parent_experiments=list(d.get("parent_experiments") or []),
            parent_strategies=list(d.get("parent_strategies") or []),
            reason=d.get("reason") or "",
            observed_failure=d.get("observed_failure") or "",
            proposed_change=d.get("proposed_change") or "",
            expected_effect=d.get("expected_effect") or "",
            falsification_criteria=fc,
            market_scope=list(d.get("market_scope") or []),
            timeframe_scope=list(d.get("timeframe_scope") or []),
            regime_scope=list(d.get("regime_scope") or []),
            mutation_type=d.get("mutation_type") or MutationType.PARAMETER.value,
            strategy_family=d.get("strategy_family") or "",
            base_strategy_spec=dict(d.get("base_strategy_spec") or {}),
            proposed_strategy_spec=dict(d.get("proposed_strategy_spec") or {}),
            evidence_summary=dict(d.get("evidence_summary") or {}),
            status=st,
            lineage=list(d.get("lineage") or []),
            created_at=float(d.get("created_at") or time.time()),
            model_provenance=d.get("model_provenance") or "quant_hypothesis_engine_v07",
            sealed=bool(d.get("sealed")),
            content_hash=d.get("content_hash") or "",
            explanation=d.get("explanation") or "",
            child_candidate_id=d.get("child_candidate_id") or "",
            child_experiment_ids=list(d.get("child_experiment_ids") or []),
        )

    def human_readable(self) -> str:
        if self.explanation:
            return self.explanation
        ev = self.evidence_summary or {}
        return "\n".join([
            f"HYPOTHESIS {self.id}",
            "",
            "Observed:",
            f"  {self.observed_failure}",
            "",
            "Evidence:",
            f"  {ev.get('n_experiments', len(self.parent_experiments))} experiments",
            f"  {ev.get('n_markets', len(self.market_scope))} markets",
            f"  {ev.get('n_timeframes', len(self.timeframe_scope))} timeframes",
            f"  parents: {', '.join(self.parent_experiments[:8]) or 'n/a'}",
            "",
            "Reason:",
            f"  {self.reason}",
            "",
            "Proposed change:",
            f"  {self.proposed_change}",
            f"  mutation_type: {self.mutation_type}",
            "",
            "Expected effect:",
            f"  {self.expected_effect}",
            "",
            "Falsification:",
            f"  - OOS Sharpe degradation > {self.falsification_criteria.max_oos_sharpe_degradation_pct}%",
            f"  - DD increase > {self.falsification_criteria.max_drawdown_increase_pct}%",
            f"  - trade count < {self.falsification_criteria.min_trades}",
            f"  - improvement absent across ≥{self.falsification_criteria.min_markets_showing_improvement} markets"
            if self.falsification_criteria.require_walk_forward_improvement else "",
            "",
            f"Status: {self.status.value if isinstance(self.status, HypothesisStatus) else self.status}",
            f"Provenance: {self.model_provenance}",
            "",
            "This is not a profitability claim. Hypothesis must re-enter the full research pipeline.",
        ])


def new_hypothesis_id() -> str:
    return f"H-{uuid.uuid4().hex[:6].upper()}"
