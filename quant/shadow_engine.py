"""
ShadowExecutionEngine — hypothetical signals/fills only.

Hard constraint: no broker trading API, no credentials, no real orders.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .data import Bar, Series
from .dsl import Strategy
from .execution_model import ExecutionModel
from .shadow_market import ShadowMarketFeed
from .shadow_trial import ShadowTrial, ShadowTrialStatus
from .shadow_trade import ShadowSignal, ShadowTrade, TRADE_KIND
from .trial import fingerprint_strategy, TrialVerdict, paper_metrics_from_trial, compute_divergence
from .backtest import run_backtest, _build_indicators, _signal
from .regime import detect_regimes
from .research_lab import ResearchLab
from .experiment import Disposition
from .data import Bar as DataBar


class ShadowEngine:
    def __init__(
        self,
        feed: Optional[ShadowMarketFeed] = None,
        execution: Optional[ExecutionModel] = None,
        research: Optional[ResearchLab] = None,
        persist_dir: Optional[Path] = None,
        *,
        max_drawdown_limit: float = 0.30,
    ):
        self.feed = feed or ShadowMarketFeed()
        self.execution = execution or ExecutionModel()
        self.research = research
        self.persist_dir = Path(persist_dir) if persist_dir else Path.home() / ".pear" / "quant_shadow"
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.trials: Dict[str, ShadowTrial] = {}
        self.max_drawdown_limit = max_drawdown_limit
        self._backtest_baseline: Dict[str, Dict[str, Any]] = {}
        # subscribe
        self.feed.subscribe(self._on_feed_bar)
        # safety: no broker handle
        self.broker = None
        self.allows_real_orders = False

    def start_trial(
        self,
        strategy: Strategy,
        symbol: str,
        *,
        baseline_series: Optional[Series] = None,
        checkpoint_days=(30, 60, 90),
        bars_per_day: float = 1.0,
        timeframe: str = "1m",
    ) -> ShadowTrial:
        trial = ShadowTrial.create(
            strategy, symbol, timeframe=timeframe,
            bars_per_day=bars_per_day, checkpoint_days=checkpoint_days,
        )
        if baseline_series is not None:
            bt = run_backtest(baseline_series, strategy)
            self._backtest_baseline[trial.id] = {
                "sharpe": bt.sharpe,
                "max_drawdown": bt.max_drawdown,
                "trades": bt.trades,
                "total_return": bt.total_return,
            }
        trial.status = ShadowTrialStatus.RUNNING
        trial.started_at = time.time()
        self.trials[trial.id] = trial
        self._save(trial)
        return trial

    def stop_trial(self, trial_id: str) -> ShadowTrial:
        t = self.trials[trial_id]
        if t.status not in (ShadowTrialStatus.COMPLETE, ShadowTrialStatus.RETIRED):
            t.status = ShadowTrialStatus.COMPLETE
            t.notes.append("stopped by operator")
            self._maybe_finalize(t, force=True)
        self._save(t)
        return t

    def status(self, trial_id: Optional[str] = None) -> Dict[str, Any]:
        if trial_id:
            t = self.trials[trial_id]
            return {
                "trial": t.to_dict(),
                "kind": TRADE_KIND,
                "allows_real_orders": False,
                "broker": None,
            }
        return {
            "active": [
                {"id": t.id, "status": t.status.value, "symbol": t.symbol, "bars": t.bar_index}
                for t in self.trials.values()
                if t.status == ShadowTrialStatus.RUNNING
            ],
            "total": len(self.trials),
            "allows_real_orders": False,
            "kind": TRADE_KIND,
        }

    def assert_fingerprint(self, trial_id: str, strategy: Strategy) -> None:
        t = self.trials[trial_id]
        fp = fingerprint_strategy(strategy.spec.to_dict())
        if fp != t.strategy_fingerprint:
            t.mutated = True
            t.status = ShadowTrialStatus.RETIRED
            t.verdict = TrialVerdict.RETIRE.value
            t.notes.append("integrity: fingerprint mismatch — retired")
            self._save(t)
            raise RuntimeError("strategy mutation during shadow trial")

    def _on_feed_bar(self, symbol: str, bar: Bar) -> None:
        for t in list(self.trials.values()):
            if t.symbol == symbol and t.status == ShadowTrialStatus.RUNNING:
                self.on_bar(t.id, bar)

    def on_bar(self, trial_id: str, bar: Bar) -> Dict[str, Any]:
        t = self.trials[trial_id]
        if t.status in (ShadowTrialStatus.COMPLETE, ShadowTrialStatus.RETIRED):
            return {"skipped": True}
        if t.mutated:
            raise RuntimeError("mutated trial")

        # server-side timestamp on every processing step
        server_ts = time.time()
        strategy = t.locked_strategy()
        t.closes.append(bar.close)
        t.bar_index += 1
        events: Dict[str, Any] = {"bar": t.bar_index, "server_ts": server_ts, "kind": TRADE_KIND}

        delay = int(self.execution.delay_bars)
        if len(t.closes) >= 15 + delay:
            inds = _build_indicators(strategy, t.closes)
            sig_i = max(1, len(t.closes) - 1 - delay)
            regime = self._regime(t.closes)

            if t.position == 0:
                if _signal(strategy.spec.entry, inds, sig_i):
                    side = "buy" if strategy.spec.side != "short" else "sell"
                    sig = ShadowSignal.create(
                        trial_id=t.id,
                        strategy_fingerprint=t.strategy_fingerprint,
                        symbol=t.symbol,
                        side=side,
                        bar_ts=bar.ts,
                        server_ts=server_ts,
                        price_observed=bar.close,
                        regime=regime,
                    )
                    t.signals.append(sig.to_dict())
                    fill = self.execution.apply_fill_price(bar.close, side)
                    trade = ShadowTrade(
                        id=f"strd_{__import__('uuid').uuid4().hex[:10]}",
                        trial_id=t.id,
                        strategy_fingerprint=t.strategy_fingerprint,
                        symbol=t.symbol,
                        side=side,
                        entry_ts=server_ts,
                        entry_bar_ts=bar.ts,
                        entry_observed=bar.close,
                        entry_fill_sim=fill,
                        regime_entry=regime,
                        spread_bps=self.execution.spread_bps,
                        slippage_bps=self.execution.slippage_bps,
                        latency_bars=delay,
                    )
                    t.position = 1 if side == "buy" else -1
                    t.entry_price = fill
                    t.open_trade_id = trade.id
                    t.trades += 1
                    t.trades_log.append(trade.to_dict())
                    events["signal"] = sig.to_dict()
                    events["open"] = trade.id
            else:
                if _signal(strategy.spec.exit, inds, sig_i):
                    side = "sell" if t.position > 0 else "buy"
                    sig = ShadowSignal.create(
                        trial_id=t.id,
                        strategy_fingerprint=t.strategy_fingerprint,
                        symbol=t.symbol,
                        side=side,
                        bar_ts=bar.ts,
                        server_ts=server_ts,
                        price_observed=bar.close,
                        regime=regime,
                    )
                    t.signals.append(sig.to_dict())
                    fill = self.execution.apply_fill_price(bar.close, side)
                    # update last open trade
                    for tr in reversed(t.trades_log):
                        if tr.get("id") == t.open_trade_id and tr.get("exit_ts") is None:
                            pos = t.position
                            tr["exit_ts"] = server_ts
                            tr["exit_bar_ts"] = bar.ts
                            tr["exit_observed"] = bar.close
                            tr["exit_fill_sim"] = fill
                            tr["regime_exit"] = regime
                            tr["server_ts_close"] = server_ts
                            tr["pnl"] = pos * (fill - t.entry_price) / t.entry_price
                            tr["kind"] = TRADE_KIND
                            ret = tr["pnl"]
                            t.equity *= 1 + ret
                            t.peak = max(t.peak, t.equity)
                            if ret >= 0:
                                t.wins += 1
                                t.gross_win += ret
                            else:
                                t.losses += 1
                                t.gross_loss += abs(ret)
                            events["close_pnl"] = ret
                            break
                    t.position = 0
                    t.open_trade_id = None

        t.equity_curve.append(t.equity)
        events.update(self._checkpoints(t))
        self._save(t)
        return events

    def _regime(self, closes: List[float]) -> str:
        if len(closes) < 20:
            return "unknown"
        series = Series("TMP", "tick", [DataBar(ts=i, open=c, high=c, low=c, close=c) for i, c in enumerate(closes[-40:])])
        labs = detect_regimes(series, lookback=8)
        return labs[-1].regime if labs else "unknown"

    def _metrics(self, t: ShadowTrial) -> Dict[str, float]:
        trades = t.trades
        win_rate = (t.wins / trades) if trades else 0.0
        pf = (t.gross_win / t.gross_loss) if t.gross_loss > 1e-12 else (10.0 if t.gross_win > 0 else 0.0)
        dd = (t.peak - t.equity) / t.peak if t.peak else 0.0
        rets = []
        for i in range(1, len(t.equity_curve)):
            if t.equity_curve[i - 1] > 0:
                rets.append((t.equity_curve[i] - t.equity_curve[i - 1]) / t.equity_curve[i - 1])
        mean = sum(rets) / len(rets) if rets else 0.0
        var = sum((x - mean) ** 2 for x in rets) / len(rets) if rets else 0.0
        std = var ** 0.5
        sharpe = (mean / std * (252 ** 0.5)) if std > 1e-12 else 0.0
        return {
            "equity": t.equity,
            "total_return": t.equity - 1.0,
            "max_drawdown": dd,
            "trades": float(trades),
            "win_rate": win_rate,
            "profit_factor": pf,
            "sharpe": sharpe,
            "days": t.day_count(),
        }

    def _checkpoints(self, t: ShadowTrial) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        m = self._metrics(t)
        if m["max_drawdown"] >= self.max_drawdown_limit:
            t.status = ShadowTrialStatus.RETIRED
            t.verdict = TrialVerdict.RETIRE.value
            t.notes.append(f"retired: shadow DD {m['max_drawdown']:.2%}")
            self._commit_memory(t, m)
            return {"retired": True}

        c30, c60, c90 = t.checkpoint_bars
        if t.bar_index >= c30 and "d30" not in t.checkpoints:
            t.checkpoints["d30"] = m
            t.status = ShadowTrialStatus.CHECKPOINT_30
            out["checkpoint"] = "30d"
        if t.bar_index >= c60 and "d60" not in t.checkpoints:
            t.checkpoints["d60"] = m
            t.status = ShadowTrialStatus.CHECKPOINT_60
            out["checkpoint"] = "60d"
        if t.bar_index >= c90 and "d90" not in t.checkpoints:
            t.checkpoints["d90"] = m
            t.status = ShadowTrialStatus.CHECKPOINT_90
            out["checkpoint"] = "90d"
            self._maybe_finalize(t, force=True)
        return out

    def _maybe_finalize(self, t: ShadowTrial, force: bool = False) -> None:
        m = self._metrics(t)
        baseline = self._backtest_baseline.get(t.id) or {}
        if baseline:
            div = compute_divergence(baseline, m)
            t.checkpoints["divergence"] = div.to_dict()
            if div.level == "HIGH":
                t.verdict = TrialVerdict.RETIRE.value
            elif div.level == "LOW" and m.get("sharpe", 0) > 0:
                t.verdict = TrialVerdict.PROMOTE.value
            else:
                t.verdict = TrialVerdict.CONTINUE.value
        else:
            t.verdict = TrialVerdict.CONTINUE.value
        if force or t.status == ShadowTrialStatus.CHECKPOINT_90:
            if t.verdict == TrialVerdict.RETIRE.value:
                t.status = ShadowTrialStatus.RETIRED
            else:
                t.status = ShadowTrialStatus.COMPLETE
            self._commit_memory(t, m)

    def _commit_memory(self, t: ShadowTrial, metrics: Dict[str, Any]) -> None:
        if self.research is None:
            return
        strategy = t.locked_strategy()
        # build minimal series for experiment dataset id
        series = Series(t.symbol, t.timeframe, [
            DataBar(ts=i, open=c, high=c, low=c, close=c) for i, c in enumerate(t.closes[-50:] or [1.0])
        ])
        exp = self.research.run_experiment(
            strategy,
            series,
            source="shadow",
            paper_metrics=metrics,  # shadow metrics stored in paper field for comparison APIs
            divergence=t.checkpoints.get("divergence"),
            execution=self.execution.to_dict(),
            auto_seal=True,
        )
        exp.notes.append(f"shadow_trial={t.id}")
        exp.tags.append("shadow")
        # re-seal not allowed — notes were before seal in run_experiment; append via memory only if unsealed
        t.notes.append(f"research_exp={exp.id}")

    def report(self, trial_id: str) -> str:
        t = self.trials[trial_id]
        m = self._metrics(t)
        div = t.checkpoints.get("divergence") or {}
        lines = [
            f"# Shadow-market validation report",
            f"Trial: {t.id} | kind={TRADE_KIND}",
            f"Fingerprint: {t.strategy_fingerprint} (LOCKED)",
            f"Symbol: {t.symbol} | Status: {t.status.value} | Verdict: {t.verdict}",
            f"Bars: {t.bar_index} (~{t.day_count():.1f} days)",
            f"Allows real orders: **False** | Broker API: **None**",
            "",
            "## Shadow metrics",
            f"- Sharpe: {m.get('sharpe', 0):.2f}",
            f"- Drawdown: {m.get('max_drawdown', 0):.2%}",
            f"- Trades: {int(m.get('trades', 0))}",
            f"- Return: {m.get('total_return', 0):.2%}",
            "",
            "## Divergence vs frozen backtest baseline",
            f"{div or 'n/a (no baseline series provided)'}",
            "",
            "## Checkpoints",
            f"- 30d: {t.checkpoints.get('d30')}",
            f"- 60d: {t.checkpoints.get('d60')}",
            f"- 90d: {t.checkpoints.get('d90')}",
            "",
            f"Signals: {len(t.signals)} | Trade records: {len(t.trades_log)}",
            "Every signal carries server_ts. Trades kind='shadow' only.",
            "",
            "No real orders were possible from this engine.",
        ]
        return "\n".join(lines)

    def _save(self, t: ShadowTrial) -> None:
        path = self.persist_dir / f"{t.id}.json"
        import json
        path.write_text(json.dumps(t.to_dict(), indent=2), encoding="utf-8")

    def push_live_bar(self, symbol: str, price: float, ts: Optional[float] = None) -> Tuple[bool, str]:
        """External market observer entrypoint."""
        return self.feed.push_price(symbol, price, ts=ts)
