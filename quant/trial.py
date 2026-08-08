"""
Frozen long-horizon paper validation trials (v0.3).

Strategy configuration is immutable for the duration of a trial.
Evaluation periods (30/60/90d) are fixed at creation — the candidate
cannot be mutated to fit the evaluation window.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .dsl import Strategy, StrategySpec, parse_strategy
from .execution_model import ExecutionModel
from .backtest import run_backtest, BacktestResult
from .data import Series
from .regime import detect_regimes, regime_summary
from .promotion import Stage


class TrialStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    CHECKPOINT_30 = "checkpoint_30"
    CHECKPOINT_60 = "checkpoint_60"
    CHECKPOINT_90 = "checkpoint_90"
    COMPLETE = "complete"
    RETIRED = "retired"


class TrialVerdict(str, Enum):
    CONTINUE = "continue"
    PROMOTE = "promote"
    RETIRE = "retire"
    HOLD = "hold"


def fingerprint_strategy(spec: dict) -> str:
    blob = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class DivergenceReport:
    backtest_sharpe: float
    paper_sharpe: float
    backtest_drawdown: float
    paper_drawdown: float
    backtest_trades: int
    paper_trades: int
    backtest_return: float
    paper_return: float
    sharpe_gap: float
    drawdown_gap: float
    trade_count_ratio: float
    level: str  # LOW | MEDIUM | HIGH

    def to_dict(self) -> dict:
        return asdict(self)


def classify_divergence(d: DivergenceReport) -> str:
    # High divergence if sharpe collapses or DD blows out
    if d.sharpe_gap > 1.0 or d.drawdown_gap > 0.10 or (
        d.backtest_sharpe > 0.5 and d.paper_sharpe < 0.3 * d.backtest_sharpe
    ):
        return "HIGH"
    if d.sharpe_gap > 0.4 or d.drawdown_gap > 0.05:
        return "MEDIUM"
    return "LOW"


@dataclass
class ValidationTrial:
    id: str
    strategy_fingerprint: str
    strategy_spec: Dict[str, Any]  # frozen snapshot
    symbol: str
    timeframe: str
    status: TrialStatus = TrialStatus.PENDING
    created_at: float = field(default_factory=time.time)
    # evaluation windows in *bars* (mapped from 30/60/90 days via bars_per_day)
    bars_per_day: float = 1.0
    checkpoint_bars: Tuple[int, int, int] = (30, 60, 90)
    bar_index: int = 0
    execution: Dict[str, Any] = field(default_factory=dict)
    backtest_metrics: Dict[str, Any] = field(default_factory=dict)
    paper_equity: float = 1.0
    paper_peak: float = 1.0
    paper_trades: int = 0
    paper_wins: int = 0
    paper_losses: int = 0
    gross_win: float = 0.0
    gross_loss: float = 0.0
    position: int = 0
    entry_price: float = 0.0
    consecutive_losses: int = 0
    max_consecutive_losses: int = 0
    closes: List[float] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    regime_pnl: Dict[str, float] = field(default_factory=dict)
    signals_log: List[Dict[str, Any]] = field(default_factory=list)
    fills_log: List[Dict[str, Any]] = field(default_factory=list)
    checkpoints: Dict[str, Any] = field(default_factory=dict)
    verdict: TrialVerdict = TrialVerdict.HOLD
    confidence: str = "low"
    notes: List[str] = field(default_factory=list)
    mutated: bool = False  # must stay False

    def locked_strategy(self) -> Strategy:
        if self.mutated:
            raise RuntimeError("trial integrity violation: strategy was mutated")
        return parse_strategy(self.strategy_spec)

    def day_count(self) -> float:
        return self.bar_index / max(self.bars_per_day, 1e-9)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        d["verdict"] = self.verdict.value
        # trim large arrays for persistence summary
        d["closes"] = self.closes[-5:]
        d["equity_curve"] = self.equity_curve[:: max(1, len(self.equity_curve) // 50)] if self.equity_curve else []
        d["signals_log"] = self.signals_log[-50:]
        d["fills_log"] = self.fills_log[-50:]
        return d


def paper_metrics_from_trial(t: ValidationTrial) -> Dict[str, float]:
    trades = t.paper_trades
    win_rate = (t.paper_wins / trades) if trades else 0.0
    pf = (t.gross_win / t.gross_loss) if t.gross_loss > 1e-12 else (10.0 if t.gross_win > 0 else 0.0)
    dd = (t.paper_peak - t.paper_equity) / t.paper_peak if t.paper_peak else 0.0
    rets = []
    for i in range(1, len(t.equity_curve)):
        if t.equity_curve[i - 1] > 0:
            rets.append((t.equity_curve[i] - t.equity_curve[i - 1]) / t.equity_curve[i - 1])
    mean = sum(rets) / len(rets) if rets else 0.0
    var = sum((x - mean) ** 2 for x in rets) / len(rets) if rets else 0.0
    std = var ** 0.5
    sharpe = (mean / std * (252 ** 0.5)) if std > 1e-12 else 0.0
    return {
        "equity": t.paper_equity,
        "total_return": t.paper_equity - 1.0,
        "max_drawdown": dd,
        "trades": float(trades),
        "win_rate": win_rate,
        "profit_factor": pf,
        "sharpe": sharpe,
        "days": t.day_count(),
        "max_consecutive_losses": float(t.max_consecutive_losses),
    }


def compute_divergence(backtest: Dict[str, Any], paper: Dict[str, Any]) -> DivergenceReport:
    bs = float(backtest.get("sharpe") or 0)
    ps = float(paper.get("sharpe") or 0)
    bd = float(backtest.get("max_drawdown") or 0)
    pd = float(paper.get("max_drawdown") or 0)
    bt = int(backtest.get("trades") or 0)
    pt = int(paper.get("trades") or 0)
    br = float(backtest.get("total_return") or 0)
    pr = float(paper.get("total_return") or 0)
    d = DivergenceReport(
        backtest_sharpe=bs,
        paper_sharpe=ps,
        backtest_drawdown=bd,
        paper_drawdown=pd,
        backtest_trades=bt,
        paper_trades=pt,
        backtest_return=br,
        paper_return=pr,
        sharpe_gap=bs - ps,
        drawdown_gap=pd - bd,
        trade_count_ratio=(pt / bt) if bt else 0.0,
        level="LOW",
    )
    d.level = classify_divergence(d)
    return d
