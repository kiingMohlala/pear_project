"""
PaperTradingEngine — virtual orders only, concurrent strategies, regime-aware metrics.
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .brokers import BrokerAdapter, SimulatedBroker, OrderRequest, get_broker
from .dsl import Strategy
from .paper_store import PaperStore
from .promotion import Stage, PromotionThresholds, evaluate_promotion, PromotionDecision
from .regime import detect_regimes
from .data import Series, Bar


@dataclass
class PaperStrategyRuntime:
    strategy_id: str
    strategy: Strategy
    symbol: str
    stage: Stage = Stage.PAPER
    qty: float = 1.0
    position: int = 0  # -1 short, 0 flat, 1 long
    entry_price: float = 0.0
    equity: float = 1.0
    peak: float = 1.0
    wins: int = 0
    losses: int = 0
    gross_win: float = 0.0
    gross_loss: float = 0.0
    trades: int = 0
    started_at: float = field(default_factory=time.time)
    last_prices: List[float] = field(default_factory=list)
    regime_pnl: Dict[str, float] = field(default_factory=dict)
    closes_window: List[float] = field(default_factory=list)


def _rolling_metrics(rt: PaperStrategyRuntime) -> Dict[str, Any]:
    trades = rt.trades
    win_rate = (rt.wins / trades) if trades else 0.0
    pf = (rt.gross_win / rt.gross_loss) if rt.gross_loss > 1e-12 else (10.0 if rt.gross_win > 0 else 0.0)
    dd = (rt.peak - rt.equity) / rt.peak if rt.peak else 0.0
    days = max(0.0, (time.time() - rt.started_at) / 86400.0)
    # expectancy per trade
    avg_win = rt.gross_win / rt.wins if rt.wins else 0.0
    avg_loss = rt.gross_loss / rt.losses if rt.losses else 0.0
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
    # crude sharpe from equity samples
    rets = []
    for i in range(1, len(rt.last_prices)):
        # reuse last_prices as equity samples if we store equity there — use closes_window equity proxy
        pass
    # store equity samples in last_prices for simplicity
    eq = rt.last_prices
    for i in range(1, len(eq)):
        if eq[i - 1] > 0:
            rets.append((eq[i] - eq[i - 1]) / eq[i - 1])
    mean = sum(rets) / len(rets) if rets else 0.0
    var = sum((x - mean) ** 2 for x in rets) / len(rets) if rets else 0.0
    std = math.sqrt(var) if var > 0 else 0.0
    sharpe = (mean / std * math.sqrt(252)) if std > 1e-12 else 0.0
    return {
        "strategy_id": rt.strategy_id,
        "equity": rt.equity,
        "max_drawdown": dd,
        "trades": trades,
        "win_rate": win_rate,
        "profit_factor": pf,
        "expectancy": expectancy,
        "sharpe": sharpe,
        "days": days,
        "trade_frequency_per_day": trades / days if days > 0 else 0.0,
        "position": rt.position,
    }


class PaperTradingEngine:
    """
    Concurrent paper trading for many strategies.
    Guarantees paper=True fills only.
    """

    def __init__(
        self,
        broker: Optional[BrokerAdapter] = None,
        store: Optional[PaperStore] = None,
        thresholds: Optional[PromotionThresholds] = None,
    ):
        self.broker = broker or SimulatedBroker()
        self.broker.connect()
        self.store = store or PaperStore()
        self.thresholds = thresholds or PromotionThresholds()
        self.runtimes: Dict[str, PaperStrategyRuntime] = {}
        self._allow_live = False  # hard off

    def register(
        self,
        strategy: Strategy,
        symbol: str,
        *,
        stage: Stage = Stage.PAPER,
        qty: float = 1.0,
        strategy_id: Optional[str] = None,
    ) -> str:
        sid = strategy_id or f"ps_{uuid.uuid4().hex[:10]}"
        rt = PaperStrategyRuntime(
            strategy_id=sid,
            strategy=strategy,
            symbol=symbol,
            stage=stage,
            qty=qty,
        )
        self.runtimes[sid] = rt
        self.store.upsert_strategy(sid, strategy.name, stage.value, symbol, dict(strategy.spec.params), {}, {})
        return sid

    def _regime_label(self, closes: List[float]) -> str:
        if len(closes) < 25:
            return "unknown"
        series = Series("TMP", "tick", [Bar(ts=i, open=c, high=c, low=c, close=c) for i, c in enumerate(closes[-50:])])
        labs = detect_regimes(series, lookback=10)
        return labs[-1].regime if labs else "unknown"

    def on_quote(self, symbol: str, price: Optional[float] = None) -> List[Dict[str, Any]]:
        """Drive all strategies for a symbol with a new quote (or broker poll)."""
        if price is not None and isinstance(self.broker, SimulatedBroker):
            self.broker.set_price(symbol, price)
        q = self.broker.get_quote(symbol)
        self.store.log_quote(symbol, q.bid, q.ask, q.ts)
        mid = q.mid
        events = []
        for rt in list(self.runtimes.values()):
            if rt.symbol != symbol or rt.stage == Stage.RETIRED:
                continue
            ev = self._step(rt, mid)
            if ev:
                events.extend(ev)
        return events

    def _step(self, rt: PaperStrategyRuntime, mid: float) -> List[Dict[str, Any]]:
        from .backtest import _build_indicators, _signal

        rt.closes_window.append(mid)
        if len(rt.closes_window) > 300:
            rt.closes_window = rt.closes_window[-300:]
        closes = rt.closes_window
        if len(closes) < 15:
            return []
        inds = _build_indicators(rt.strategy, closes)
        i = len(closes) - 1
        regime = self._regime_label(closes)
        events = []

        if rt.position == 0:
            if _signal(rt.strategy.spec.entry, inds, i):
                side = "buy" if rt.strategy.spec.side != "short" else "sell"
                oid = f"ord_{uuid.uuid4().hex[:8]}"
                self.store.log_signal(f"sig_{uuid.uuid4().hex[:8]}", rt.strategy_id, rt.symbol, side, 1.0, regime, {})
                self.store.log_order(oid, rt.strategy_id, rt.symbol, side, rt.qty, "submitted")
                fill = self.broker.place_order(OrderRequest(rt.symbol, side, rt.qty, client_id=oid, strategy_id=rt.strategy_id))
                if not fill.paper:
                    raise RuntimeError("non-paper fill blocked")
                self.store.log_fill(fill.id, oid, rt.strategy_id, fill.symbol, fill.side, fill.qty, fill.price)
                rt.position = 1 if side == "buy" else -1
                rt.entry_price = fill.price
                rt.trades += 1
                events.append({"type": "enter", "strategy_id": rt.strategy_id, "price": fill.price, "regime": regime})
        else:
            if _signal(rt.strategy.spec.exit, inds, i):
                side = "sell" if rt.position > 0 else "buy"
                oid = f"ord_{uuid.uuid4().hex[:8]}"
                self.store.log_order(oid, rt.strategy_id, rt.symbol, side, rt.qty, "submitted")
                fill = self.broker.place_order(OrderRequest(rt.symbol, side, rt.qty, client_id=oid, strategy_id=rt.strategy_id))
                if not fill.paper:
                    raise RuntimeError("non-paper fill blocked")
                self.store.log_fill(fill.id, oid, rt.strategy_id, fill.symbol, fill.side, fill.qty, fill.price)
                ret = rt.position * (fill.price - rt.entry_price) / rt.entry_price
                rt.equity *= 1 + ret
                rt.peak = max(rt.peak, rt.equity)
                if ret >= 0:
                    rt.wins += 1
                    rt.gross_win += ret
                else:
                    rt.losses += 1
                    rt.gross_loss += abs(ret)
                rt.regime_pnl[regime] = rt.regime_pnl.get(regime, 0.0) + ret
                rt.position = 0
                events.append({"type": "exit", "strategy_id": rt.strategy_id, "ret": ret, "regime": regime})

        dd = (rt.peak - rt.equity) / rt.peak if rt.peak else 0.0
        rt.last_prices.append(rt.equity)
        if len(rt.last_prices) > 500:
            rt.last_prices = rt.last_prices[-500:]
        self.store.log_equity(rt.strategy_id, rt.equity, dd)
        metrics = _rolling_metrics(rt)
        self.store.upsert_strategy(
            rt.strategy_id, rt.strategy.name, rt.stage.value, rt.symbol,
            dict(rt.strategy.spec.params), metrics, dict(rt.regime_pnl),
        )
        return events

    def run_price_path(self, symbol: str, prices: List[float]) -> int:
        """Replay a price path for reproducible paper validation."""
        n = 0
        for p in prices:
            self.on_quote(symbol, p)
            n += 1
        return n

    def metrics(self, strategy_id: str) -> Dict[str, Any]:
        rt = self.runtimes[strategy_id]
        return _rolling_metrics(rt)

    def promote_check(self, strategy_id: str) -> PromotionDecision:
        rt = self.runtimes[strategy_id]
        m = _rolling_metrics(rt)
        decision = evaluate_promotion(rt.stage, m, self.thresholds)
        if decision.action == "promote":
            rt.stage = Stage(decision.to_stage)
        elif decision.action == "demote":
            rt.stage = Stage(decision.to_stage)
        elif decision.action == "retire":
            rt.stage = Stage.RETIRED
        self.store.upsert_strategy(
            rt.strategy_id, rt.strategy.name, rt.stage.value, rt.symbol,
            dict(rt.strategy.spec.params), m, dict(rt.regime_pnl),
        )
        return decision

    def promote_all(self) -> List[PromotionDecision]:
        return [self.promote_check(sid) for sid in list(self.runtimes.keys())]

    def dashboard_data(self) -> Dict[str, Any]:
        rows = self.store.list_strategies()
        active = [r for r in rows if r["stage"] != Stage.RETIRED.value]
        rankings = sorted(
            active,
            key=lambda r: -float((r.get("metrics") or {}).get("sharpe") or 0),
        )
        return {
            "active": len(active),
            "total": len(rows),
            "by_stage": {
                s.value: sum(1 for r in rows if r["stage"] == s.value) for s in Stage
            },
            "rankings": rankings[:50],
            "disclaimer": "Paper trading only — no real orders. Historical/virtual performance is not a prediction.",
        }

    def validation_report(self, strategy_id: str, period: str = "weekly") -> str:
        rt = self.runtimes.get(strategy_id)
        m = self.metrics(strategy_id) if rt else {}
        fills = self.store.fills_for(strategy_id)
        lines = [
            f"# Paper validation report ({period})",
            f"Strategy: {strategy_id}",
            f"Stage: {rt.stage.value if rt else 'n/a'}",
            f"Trades/fills: {m.get('trades')} / {len(fills)}",
            f"Equity: {m.get('equity', 0):.4f}",
            f"Sharpe: {m.get('sharpe', 0):.2f}",
            f"Max DD: {m.get('max_drawdown', 0):.2%}",
            f"Win rate: {m.get('win_rate', 0):.2%}",
            f"Profit factor: {m.get('profit_factor', 0):.2f}",
            f"Expectancy: {m.get('expectancy', 0):.4f}",
            f"Trade frequency / day: {m.get('trade_frequency_per_day', 0):.2f}",
            f"Days: {m.get('days', 0):.2f}",
            f"Regime PnL: {rt.regime_pnl if rt else {}}",
            "",
            "No real orders were placed. Reproducible from stored quotes and fills.",
        ]
        return "\n".join(lines)
