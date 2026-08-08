"""Strategy knowledge base — performance by market, timeframe, regime."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class StrategyKnowledgeBase:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else Path.home() / ".pear" / "quant_kb.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self.records = json.loads(self.path.read_text(encoding="utf-8")).get("records") or []
            except Exception:
                self.records = []

    def _save(self) -> None:
        self.path.write_text(json.dumps({"records": self.records[-2000:], "updated": time.time()}, indent=2), encoding="utf-8")

    def add(
        self,
        strategy_name: str,
        *,
        symbol: str,
        timeframe: str,
        metrics: Dict[str, Any],
        regimes: Optional[Dict[str, float]] = None,
        passed: bool = False,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.records.append({
            "strategy": strategy_name,
            "symbol": symbol,
            "timeframe": timeframe,
            "metrics": metrics,
            "regimes": regimes or {},
            "passed": passed,
            "params": params or {},
            "ts": time.time(),
        })
        self._save()

    def best_for(self, symbol: str = "", timeframe: str = "", limit: int = 10) -> List[Dict[str, Any]]:
        rows = self.records
        if symbol:
            rows = [r for r in rows if r.get("symbol") == symbol]
        if timeframe:
            rows = [r for r in rows if r.get("timeframe") == timeframe]
        rows = [r for r in rows if r.get("passed")]
        rows.sort(key=lambda r: -float((r.get("metrics") or {}).get("sharpe") or 0))
        return rows[:limit]

    def recommend_conditions(self, strategy_name: str) -> Dict[str, Any]:
        rows = [r for r in self.records if r.get("strategy") == strategy_name and r.get("passed")]
        if not rows:
            return {"strategy": strategy_name, "markets": [], "note": "insufficient robust evidence"}
        by_sym: Dict[str, List[float]] = {}
        regime_acc: Dict[str, float] = {}
        for r in rows:
            by_sym.setdefault(r["symbol"], []).append(float((r.get("metrics") or {}).get("sharpe") or 0))
            for k, v in (r.get("regimes") or {}).items():
                regime_acc[k] = regime_acc.get(k, 0) + float(v)
        markets = sorted(
            [{"symbol": s, "avg_sharpe": sum(v) / len(v)} for s, v in by_sym.items()],
            key=lambda x: -x["avg_sharpe"],
        )
        n = max(1, len(rows))
        regimes = {k: v / n for k, v in regime_acc.items()}
        top_regime = max(regimes, key=regimes.get) if regimes else "unknown"
        return {
            "strategy": strategy_name,
            "markets": markets,
            "dominant_regime_share": regimes,
            "strongest_when": top_regime,
            "disclaimer": "Historical robustness only — not a prediction of future prices.",
        }
