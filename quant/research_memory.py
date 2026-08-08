"""Persistent research memory — search, patterns, family performance."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .experiment import ExperimentRecord, Disposition


class ResearchMemory:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else Path.home() / ".pear" / "quant_research_memory.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.experiments: Dict[str, ExperimentRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for d in data.get("experiments") or []:
                rec = ExperimentRecord.from_dict(d)
                self.experiments[rec.id] = rec
        except Exception:
            self.experiments = {}

    def _save(self) -> None:
        payload = {
            "experiments": [e.to_dict() for e in self.experiments.values()],
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def add(self, exp: ExperimentRecord) -> None:
        if exp.id in self.experiments and self.experiments[exp.id].sealed:
            raise RuntimeError("cannot overwrite sealed experiment")
        self.experiments[exp.id] = exp
        self._save()

    def get(self, exp_id: str) -> ExperimentRecord:
        return self.experiments[exp_id]

    def all(self) -> List[ExperimentRecord]:
        return list(self.experiments.values())

    def similar_experiments(
        self,
        *,
        strategy_fingerprint: Optional[str] = None,
        family: Optional[str] = None,
        market: Optional[str] = None,
        timeframe: Optional[str] = None,
        limit: int = 20,
    ) -> List[ExperimentRecord]:
        rows = self.all()
        if strategy_fingerprint:
            rows = [e for e in rows if e.strategy_fingerprint == strategy_fingerprint]
        if family:
            rows = [e for e in rows if e.strategy_family == family]
        if market:
            rows = [e for e in rows if e.market == market]
        if timeframe:
            rows = [e for e in rows if e.timeframe == timeframe]
        rows.sort(key=lambda e: -e.created_at)
        return rows[:limit]

    def failure_patterns(self, limit: int = 15) -> List[Dict[str, Any]]:
        counter: Counter = Counter()
        by_reason: Dict[str, List[str]] = defaultdict(list)
        for e in self.all():
            for r in e.failure_reasons:
                counter[r] += 1
                by_reason[r].append(e.id)
            if e.disposition in (Disposition.FAILED, Disposition.RETIRED) and not e.failure_reasons:
                counter["unspecified_failure"] += 1
        return [
            {"reason": reason, "count": count, "example_ids": by_reason.get(reason, [])[:5]}
            for reason, count in counter.most_common(limit)
        ]

    def best_conditions(self, strategy_family: str, limit: int = 10) -> List[Dict[str, Any]]:
        rows = [
            e for e in self.all()
            if e.strategy_family == strategy_family
            and e.disposition in (Disposition.SURVIVED, Disposition.PROMOTED, Disposition.CONTINUED)
        ]
        scored = []
        for e in rows:
            sharpe = float((e.paper or e.oos or e.backtest).get("sharpe") or 0)
            scored.append({
                "experiment_id": e.id,
                "market": e.market,
                "timeframe": e.timeframe,
                "sharpe": sharpe,
                "regimes": e.regimes,
                "disposition": e.disposition.value,
                "divergence": e.divergence.get("level"),
            })
        scored.sort(key=lambda x: -x["sharpe"])
        return scored[:limit]

    def family_performance(self, strategy_family: str) -> Dict[str, Any]:
        rows = [e for e in self.all() if e.strategy_family == strategy_family]
        if not rows:
            return {"family": strategy_family, "n": 0}
        disp = Counter(e.disposition.value for e in rows)
        markets = Counter(e.market for e in rows)
        return {
            "family": strategy_family,
            "n": len(rows),
            "dispositions": dict(disp),
            "markets": dict(markets),
            "failure_patterns": [
                p for p in self.failure_patterns()
                if any(e.strategy_family == strategy_family and p["reason"] in e.failure_reasons for e in rows)
            ][:5],
        }

    def market_summary(self, market: str) -> Dict[str, Any]:
        rows = [e for e in self.all() if e.market == market]
        families = Counter(e.strategy_family for e in rows)
        survived = sum(1 for e in rows if e.disposition in (Disposition.SURVIVED, Disposition.PROMOTED))
        return {
            "market": market,
            "experiments": len(rows),
            "survived_or_promoted": survived,
            "families": dict(families),
            "top_failures": self.failure_patterns(5),
        }

    def research_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = sorted(self.all(), key=lambda e: -e.created_at)[:limit]
        return [
            {
                "id": e.id,
                "family": e.strategy_family,
                "market": e.market,
                "timeframe": e.timeframe,
                "disposition": e.disposition.value,
                "fingerprint": e.strategy_fingerprint,
                "created_at": e.created_at,
                "sealed": e.sealed,
            }
            for e in rows
        ]

    def strategy_market_matrix(self, fingerprint: str = "", family: str = "") -> Dict[str, Any]:
        rows = self.all()
        if fingerprint:
            rows = [e for e in rows if e.strategy_fingerprint == fingerprint]
        if family:
            rows = [e for e in rows if e.strategy_family == family]
        grid: Dict[str, Dict[str, list]] = {}
        for e in rows:
            grid.setdefault(e.market, {}).setdefault(e.timeframe, []).append({
                "id": e.id,
                "disposition": e.disposition.value,
                "sharpe": (e.paper or e.backtest).get("sharpe"),
                "divergence": (e.divergence or {}).get("level"),
            })
        return grid

    def strategy_regime_summary(self, family: str = "") -> Dict[str, Any]:
        from collections import defaultdict
        acc: Dict[str, list] = defaultdict(list)
        for e in self.all():
            if family and e.strategy_family != family:
                continue
            for reg, share in (e.regimes or {}).items():
                acc[reg].append(float(share))
        return {k: (sum(v) / len(v) if v else 0.0) for k, v in acc.items()}
