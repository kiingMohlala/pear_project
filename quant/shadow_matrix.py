"""
Multi-market shadow matrix runner.

Each cell is an independent shadow trial with a frozen candidate fingerprint.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .dsl import Strategy
from .data import Series, synthetic_ohlcv
from .shadow_engine import ShadowEngine
from .shadow_trial import ShadowTrial
from .execution_model import ExecutionModel
from .universe import MatrixCell, MarketSpec, TimeframeSpec, build_matrix
from .comparison import CellResult, comparative_report, rank_cells, rank_markets, rank_timeframes
from .regime_analysis import aggregate_regime_pnl, bar_regimes
from .research_lab import ResearchLab
from .trial import fingerprint_strategy


@dataclass
class MatrixRunResult:
    cells: List[CellResult] = field(default_factory=list)
    trials: Dict[str, str] = field(default_factory=dict)  # cell_key → trial_id
    report: str = ""

    def to_dict(self) -> dict:
        return {
            "n_cells": len(self.cells),
            "cells": [c.to_dict() for c in self.cells],
            "trials": self.trials,
            "report": self.report,
        }


class ShadowMatrix:
    """
    Run the same frozen strategy across market × timeframe series.
    Series are provided by the caller (live replay or synthetic per symbol).
    """

    def __init__(
        self,
        execution: Optional[ExecutionModel] = None,
        research: Optional[ResearchLab] = None,
        persist_dir: Optional[Path] = None,
    ):
        self.execution = execution or ExecutionModel()
        self.research = research
        self.persist_dir = Path(persist_dir) if persist_dir else Path.home() / ".pear" / "quant_matrix"
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.engines: Dict[str, ShadowEngine] = {}

    def run(
        self,
        strategy: Strategy,
        series_map: Dict[Tuple[str, str], Series],
        *,
        checkpoint_days: Tuple[int, int, int] = (30, 60, 90),
        baseline_frac: float = 0.4,
    ) -> MatrixRunResult:
        """
        series_map keys: (symbol, timeframe) → Series used as the live shadow path.
        Candidate is never mutated; each cell gets its own engine/trial.
        """
        fp = fingerprint_strategy(strategy.spec.to_dict())
        # verify freeze: all cells share same fp
        cells_out: List[CellResult] = []
        trials: Dict[str, str] = {}

        for (symbol, timeframe), series in series_map.items():
            if not series.bars:
                continue
            cut = max(20, int(len(series.bars) * baseline_frac))
            baseline = Series(series.symbol, series.timeframe, series.bars[:cut])
            live = Series(series.symbol, series.timeframe, series.bars[cut:])
            # scale checkpoints to available length for offline tests
            n_live = len(live.bars)
            c30 = max(5, n_live // 4)
            c60 = max(10, n_live // 2)
            c90 = max(15, max(n_live - 1, n_live))

            eng = ShadowEngine(
                execution=self.execution,
                research=self.research,
                persist_dir=self.persist_dir / f"{symbol}_{timeframe}",
                max_drawdown_limit=0.5,
            )
            trial = eng.start_trial(
                strategy,
                symbol,
                baseline_series=baseline,
                checkpoint_days=(c30, c60, c90),
                bars_per_day=1.0,  # treat each bar as one unit for offline matrix
                timeframe=timeframe,
            )
            # integrity
            eng.assert_fingerprint(trial.id, strategy)
            for b in live.bars:
                eng.on_bar(trial.id, b)
            # force finalize if needed
            t = eng.trials[trial.id]
            if t.status.value not in ("complete", "retired"):
                eng.stop_trial(trial.id)
                t = eng.trials[trial.id]

            metrics = eng._metrics(t)
            div = (t.checkpoints.get("divergence") or {}).get("level", "UNKNOWN")
            regime_stats = aggregate_regime_pnl(t.trades_log)
            cell = CellResult(
                fingerprint=fp,
                strategy_name=strategy.name,
                market=symbol,
                timeframe=timeframe,
                metrics=metrics,
                divergence_level=str(div),
                regime_stats=regime_stats,
                trades=int(metrics.get("trades") or 0),
                trial_id=trial.id,
            )
            cells_out.append(cell)
            trials[f"{fp}:{symbol}:{timeframe}"] = trial.id
            self.engines[trial.id] = eng

            # extend research memory relationships
            if self.research is not None:
                self._index_relationships(strategy, cell)

        report = comparative_report(cells_out, min_trades=3)
        return MatrixRunResult(cells=cells_out, trials=trials, report=report)

    def _index_relationships(self, strategy: Strategy, cell: CellResult) -> None:
        mem = self.research.memory
        # store lightweight relationship tags on a sealed experiment via run_experiment already;
        # also maintain aggregate index file
        idx_path = self.persist_dir / "relationships.json"
        import json
        data = {"market_strategy": [], "timeframe_strategy": [], "regime_strategy": []}
        if idx_path.exists():
            try:
                data = json.loads(idx_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        data.setdefault("market_strategy", []).append({
            "market": cell.market,
            "fingerprint": cell.fingerprint,
            "name": cell.strategy_name,
            "score": cell.score(),
            "timeframe": cell.timeframe,
        })
        data.setdefault("timeframe_strategy", []).append({
            "timeframe": cell.timeframe,
            "fingerprint": cell.fingerprint,
            "score": cell.score(),
            "market": cell.market,
        })
        for reg, st in (cell.regime_stats or {}).items():
            data.setdefault("regime_strategy", []).append({
                "regime": reg,
                "fingerprint": cell.fingerprint,
                "avg_pnl": st.get("avg_pnl"),
                "n": st.get("n"),
                "market": cell.market,
                "timeframe": cell.timeframe,
            })
        # cap growth
        for k in data:
            data[k] = data[k][-500:]
        idx_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def synthetic_universe_series(
    symbols: List[str],
    timeframes: List[str],
    n: int = 120,
    seed: int = 0,
) -> Dict[Tuple[str, str], Series]:
    """Offline helper: distinct synthetic paths per market/TF."""
    out = {}
    for i, sym in enumerate(symbols):
        for j, tf in enumerate(timeframes):
            s = synthetic_ohlcv(n=n, seed=seed + i * 10 + j, symbol=sym, timeframe=tf)
            out[(sym, tf)] = s
    return out
