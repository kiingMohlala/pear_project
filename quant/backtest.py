"""Backtesting engine for rule-based strategies."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from .data import Series
from .dsl import Strategy
from .indicators import sma, ema, roc


@dataclass
class BacktestResult:
    strategy_name: str
    symbol: str
    timeframe: str
    n_bars: int
    trades: int
    total_return: float
    max_drawdown: float
    win_rate: float
    sharpe: float  # simple, annualized-ish for daily
    profit_factor: float
    equity_curve: List[float] = field(default_factory=list)
    trade_log: List[Dict[str, Any]] = field(default_factory=list)
    params: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        # keep equity short in serialization
        if len(d.get("equity_curve") or []) > 50:
            d["equity_curve"] = d["equity_curve"][:: max(1, len(d["equity_curve"]) // 50)]
        return d

    @property
    def score_vector(self) -> Dict[str, float]:
        return {
            "return": self.total_return,
            "drawdown": -self.max_drawdown,  # higher better
            "sharpe": self.sharpe,
            "win_rate": self.win_rate,
            "stability": max(0.0, 1.0 - self.max_drawdown),
        }


def _build_indicators(strategy: Strategy, closes: List[float]) -> Dict[str, List[Optional[float]]]:
    out: Dict[str, List[Optional[float]]] = {}
    for name, spec in strategy.spec.indicators.items():
        t = (spec or {}).get("type", "sma")
        period_key = (spec or {}).get("period", "fast")
        if isinstance(period_key, (int, float)):
            period = max(2, int(period_key))
        else:
            period = strategy.resolve_period(str(period_key), 10)
        if t == "sma":
            out[name] = sma(closes, period)
        elif t == "ema":
            out[name] = ema(closes, period)
        elif t == "roc":
            out[name] = roc(closes, period)
        else:
            out[name] = sma(closes, period)
    return out


def _signal(rule: Dict[str, Any], inds: Dict[str, List[Optional[float]]], i: int) -> bool:
    t = (rule or {}).get("type", "always")
    if t == "always":
        return True
    if t == "hold":
        return False
    if t == "opposite":
        return False
    a_name = rule.get("a")
    b_name = rule.get("b")
    a = inds.get(a_name or "", [None])[i] if a_name else None
    b = inds.get(b_name or "", [None])[i] if b_name else None
    if a is None or b is None:
        return False
    if t == "above":
        return a > b
    if t == "below":
        return a < b
    if t in ("cross_above", "cross_below") and i == 0:
        return False
    if t == "cross_above":
        pa = inds[a_name][i - 1]
        pb = inds[b_name][i - 1]
        if pa is None or pb is None:
            return False
        return pa <= pb and a > b
    if t == "cross_below":
        pa = inds[a_name][i - 1]
        pb = inds[b_name][i - 1]
        if pa is None or pb is None:
            return False
        return pa >= pb and a < b
    return False


def run_backtest(series: Series, strategy: Strategy, fee_bps: float = 1.0) -> BacktestResult:
    closes = series.closes()
    n = len(closes)
    if n < 10:
        return BacktestResult(
            strategy_name=strategy.name, symbol=series.symbol, timeframe=series.timeframe,
            n_bars=n, trades=0, total_return=0.0, max_drawdown=0.0, win_rate=0.0,
            sharpe=0.0, profit_factor=0.0, params=dict(strategy.spec.params),
        )
    inds = _build_indicators(strategy, closes)
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    position = 0  # 0 flat, 1 long, -1 short
    entry_price = 0.0
    trades = 0
    wins = 0
    gross_win = 0.0
    gross_loss = 0.0
    curve = [1.0]
    log: List[Dict[str, Any]] = []
    fee = fee_bps / 10000.0
    side = strategy.spec.side

    for i in range(1, n):
        if position == 0:
            if _signal(strategy.spec.entry, inds, i):
                position = 1 if side != "short" else -1
                entry_price = closes[i]
                equity *= (1 - fee)
                trades += 1
                log.append({"i": i, "action": "enter", "side": position, "price": entry_price})
        else:
            exit_hit = _signal(strategy.spec.exit, inds, i)
            if strategy.spec.exit.get("type") == "opposite":
                exit_hit = _signal(strategy.spec.entry, inds, i)  # flip
            if exit_hit:
                ret = position * (closes[i] - entry_price) / entry_price
                ret -= fee
                equity *= (1 + ret)
                if ret >= 0:
                    wins += 1
                    gross_win += ret
                else:
                    gross_loss += abs(ret)
                log.append({"i": i, "action": "exit", "ret": ret, "equity": equity})
                position = 0
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak else 0.0
        max_dd = max(max_dd, dd)
        curve.append(equity)

    # close open
    if position != 0:
        ret = position * (closes[-1] - entry_price) / entry_price
        equity *= (1 + ret - fee)

    rets = []
    for i in range(1, len(curve)):
        if curve[i - 1] > 0:
            rets.append((curve[i] - curve[i - 1]) / curve[i - 1])
    mean_r = sum(rets) / len(rets) if rets else 0.0
    var = sum((r - mean_r) ** 2 for r in rets) / len(rets) if rets else 0.0
    std = var ** 0.5
    sharpe = (mean_r / std * (252 ** 0.5)) if std > 1e-12 else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 1e-12 else (10.0 if gross_win > 0 else 0.0)

    return BacktestResult(
        strategy_name=strategy.name,
        symbol=series.symbol,
        timeframe=series.timeframe,
        n_bars=n,
        trades=trades,
        total_return=equity - 1.0,
        max_drawdown=max_dd,
        win_rate=(wins / trades) if trades else 0.0,
        sharpe=sharpe,
        profit_factor=pf,
        equity_curve=curve,
        trade_log=log[-100:],
        params=dict(strategy.spec.params),
    )


def run_many(series: Series, strategies: List[Strategy], fee_bps: float = 1.0) -> List[BacktestResult]:
    return [run_backtest(series, s, fee_bps=fee_bps) for s in strategies]
