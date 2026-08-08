"""
Long-horizon paper validation (Quant v0.3).

Research → frozen candidate → 30/60/90d paper → independent evaluation → promote/retire.

No self-modification of candidates during a trial. No real-money execution.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .trial import (
    ValidationTrial,
    TrialStatus,
    TrialVerdict,
    fingerprint_strategy,
    paper_metrics_from_trial,
    compute_divergence,
)
from .dsl import Strategy
from .data import Series
from .backtest import run_backtest
from .execution_model import ExecutionModel
from .market_data import MarketDataStore
from .regime import detect_regimes
from .backtest import _build_indicators, _signal


class LongHorizonValidator:
    """
    Manages frozen validation trials with costed execution and divergence scoring.
    """

    def __init__(
        self,
        store_dir: Optional[Path] = None,
        execution: Optional[ExecutionModel] = None,
        market: Optional[MarketDataStore] = None,
        *,
        max_drawdown_limit: float = 0.25,
        max_consecutive_losses: int = 8,
        min_paper_sharpe_90: float = 0.1,
        max_divergence_for_promote: str = "MEDIUM",  # HIGH blocks promote
    ):
        self.store_dir = Path(store_dir) if store_dir else Path.home() / ".pear" / "quant_trials"
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.execution = execution or ExecutionModel()
        self.market = market or MarketDataStore(self.store_dir / "market.db")
        self.trials: Dict[str, ValidationTrial] = {}
        self.max_drawdown_limit = max_drawdown_limit
        self.max_consecutive_losses = max_consecutive_losses
        self.min_paper_sharpe_90 = min_paper_sharpe_90
        self.max_divergence_for_promote = max_divergence_for_promote
        self._load_index()

    def _index_path(self) -> Path:
        return self.store_dir / "trials_index.json"

    def _trial_path(self, tid: str) -> Path:
        return self.store_dir / f"{tid}.json"

    def _load_index(self) -> None:
        p = self._index_path()
        if not p.exists():
            return
        try:
            ids = json.loads(p.read_text(encoding="utf-8")).get("ids") or []
            for tid in ids:
                tp = self._trial_path(tid)
                if tp.exists():
                    data = json.loads(tp.read_text(encoding="utf-8"))
                    # restore minimal runtime state for resume
                    t = ValidationTrial(
                        id=data["id"],
                        strategy_fingerprint=data["strategy_fingerprint"],
                        strategy_spec=data["strategy_spec"],
                        symbol=data["symbol"],
                        timeframe=data.get("timeframe", "1d"),
                        status=TrialStatus(data.get("status", "pending")),
                        created_at=float(data.get("created_at") or time.time()),
                        bars_per_day=float(data.get("bars_per_day") or 1),
                        checkpoint_bars=tuple(data.get("checkpoint_bars") or (30, 60, 90)),
                        bar_index=int(data.get("bar_index") or 0),
                        execution=data.get("execution") or self.execution.to_dict(),
                        backtest_metrics=data.get("backtest_metrics") or {},
                        paper_equity=float(data.get("paper_equity") or 1),
                        paper_peak=float(data.get("paper_peak") or 1),
                        paper_trades=int(data.get("paper_trades") or 0),
                        paper_wins=int(data.get("paper_wins") or 0),
                        paper_losses=int(data.get("paper_losses") or 0),
                        gross_win=float(data.get("gross_win") or 0),
                        gross_loss=float(data.get("gross_loss") or 0),
                        position=int(data.get("position") or 0),
                        entry_price=float(data.get("entry_price") or 0),
                        consecutive_losses=int(data.get("consecutive_losses") or 0),
                        max_consecutive_losses=int(data.get("max_consecutive_losses") or 0),
                        closes=list(data.get("closes_full") or data.get("closes") or []),
                        equity_curve=list(data.get("equity_curve_full") or data.get("equity_curve") or []),
                        regime_pnl=dict(data.get("regime_pnl") or {}),
                        signals_log=list(data.get("signals_log") or []),
                        fills_log=list(data.get("fills_log") or []),
                        checkpoints=dict(data.get("checkpoints") or {}),
                        verdict=TrialVerdict(data.get("verdict") or "hold"),
                        confidence=data.get("confidence") or "low",
                        notes=list(data.get("notes") or []),
                        mutated=bool(data.get("mutated")),
                    )
                    self.trials[tid] = t
        except Exception:
            pass

    def _save_trial(self, t: ValidationTrial) -> None:
        payload = t.to_dict()
        # persist full curves for recovery (cap size)
        payload["closes_full"] = t.closes[-2000:]
        payload["equity_curve_full"] = t.equity_curve[-2000:]
        self._trial_path(t.id).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        ids = list(self.trials.keys())
        self._index_path().write_text(json.dumps({"ids": ids}), encoding="utf-8")

    def create_trial(
        self,
        strategy: Strategy,
        series_for_backtest: Series,
        *,
        symbol: Optional[str] = None,
        bars_per_day: float = 1.0,
        checkpoint_days: Tuple[int, int, int] = (30, 60, 90),
    ) -> ValidationTrial:
        """
        Freeze strategy + capture independent backtest baseline.
        Checkpoint bars derived from days * bars_per_day (evaluation schedule fixed).
        """
        symbol = symbol or series_for_backtest.symbol
        spec = strategy.spec.to_dict()
        fp = fingerprint_strategy(spec)
        # baseline backtest WITHOUT knowing future paper path
        bt = run_backtest(series_for_backtest, strategy)
        c30, c60, c90 = checkpoint_days
        trial = ValidationTrial(
            id=f"trial_{uuid.uuid4().hex[:12]}",
            strategy_fingerprint=fp,
            strategy_spec=spec,
            symbol=symbol,
            timeframe=series_for_backtest.timeframe,
            status=TrialStatus.PENDING,
            bars_per_day=bars_per_day,
            checkpoint_bars=(
                int(c30 * bars_per_day),
                int(c60 * bars_per_day),
                int(c90 * bars_per_day),
            ),
            execution=self.execution.to_dict(),
            backtest_metrics={
                "sharpe": bt.sharpe,
                "max_drawdown": bt.max_drawdown,
                "trades": bt.trades,
                "total_return": bt.total_return,
                "win_rate": bt.win_rate,
                "profit_factor": bt.profit_factor,
            },
        )
        self.trials[trial.id] = trial
        self._save_trial(trial)
        return trial

    def assert_not_mutated(self, trial_id: str, strategy: Strategy) -> None:
        t = self.trials[trial_id]
        fp = fingerprint_strategy(strategy.spec.to_dict())
        if fp != t.strategy_fingerprint:
            t.mutated = True
            t.notes.append("INTEGRITY: strategy fingerprint changed during trial")
            t.status = TrialStatus.RETIRED
            t.verdict = TrialVerdict.RETIRE
            self._save_trial(t)
            raise RuntimeError("candidate modified during active evaluation — trial retired")

    def start(self, trial_id: str) -> ValidationTrial:
        t = self.trials[trial_id]
        if t.mutated:
            raise RuntimeError("cannot start mutated trial")
        t.status = TrialStatus.RUNNING
        self._save_trial(t)
        return t

    def on_bar(self, trial_id: str, close: float, high: Optional[float] = None, low: Optional[float] = None) -> Dict[str, Any]:
        """Process one market bar for a frozen trial. Returns event dict."""
        t = self.trials[trial_id]
        if t.status in (TrialStatus.COMPLETE, TrialStatus.RETIRED):
            return {"skipped": True, "status": t.status.value}
        if t.mutated:
            raise RuntimeError("trial integrity violation")

        strategy = t.locked_strategy()
        t.closes.append(close)
        t.bar_index += 1
        events: Dict[str, Any] = {"bar": t.bar_index}

        # delayed signal queue: signal at i → execute after delay_bars
        delay = int(self.execution.delay_bars)

        if len(t.closes) >= 15 + delay:
            inds = _build_indicators(strategy, t.closes)
            sig_i = len(t.closes) - 1 - delay
            if sig_i < 1:
                sig_i = 1

            # detect look-ahead: we only use indicators up to sig_i
            if t.position == 0:
                if _signal(strategy.spec.entry, inds, sig_i):
                    side = "buy" if strategy.spec.side != "short" else "sell"
                    # fill at *current* bar (after delay), costed
                    px = self.execution.apply_fill_price(close, side)
                    t.signals_log.append({"bar": t.bar_index, "side": side, "type": "entry"})
                    # duplicate signal guard
                    if t.signals_log[-2:] and len(t.signals_log) >= 2:
                        prev = t.signals_log[-2]
                        if prev.get("bar") == t.bar_index and prev.get("side") == side:
                            t.notes.append("duplicate signal suppressed")
                        else:
                            self._enter(t, side, px)
                    else:
                        self._enter(t, side, px)
                    events["entry"] = px
            else:
                if _signal(strategy.spec.exit, inds, sig_i):
                    side = "sell" if t.position > 0 else "buy"
                    px = self.execution.apply_fill_price(close, side)
                    ret = t.position * (px - t.entry_price) / t.entry_price
                    t.paper_equity *= 1 + ret
                    t.paper_peak = max(t.paper_peak, t.paper_equity)
                    if ret >= 0:
                        t.paper_wins += 1
                        t.gross_win += ret
                        t.consecutive_losses = 0
                    else:
                        t.paper_losses += 1
                        t.gross_loss += abs(ret)
                        t.consecutive_losses += 1
                        t.max_consecutive_losses = max(t.max_consecutive_losses, t.consecutive_losses)
                    # regime
                    series_like = Series(t.symbol, t.timeframe, [])
                    # lightweight regime on closes
                    from .data import Bar
                    series_like.bars = [Bar(ts=i, open=c, high=c, low=c, close=c) for i, c in enumerate(t.closes[-40:])]
                    labs = detect_regimes(series_like, lookback=8)
                    reg = labs[-1].regime if labs else "unknown"
                    t.regime_pnl[reg] = t.regime_pnl.get(reg, 0.0) + ret
                    t.fills_log.append({"bar": t.bar_index, "side": side, "price": px, "ret": ret, "regime": reg})
                    t.position = 0
                    events["exit"] = {"price": px, "ret": ret}

        t.equity_curve.append(t.paper_equity)
        events.update(self._check_risk_and_checkpoints(t))
        self._save_trial(t)
        return events

    def _enter(self, t: ValidationTrial, side: str, px: float) -> None:
        t.position = 1 if side == "buy" else -1
        t.entry_price = px
        t.paper_trades += 1
        t.fills_log.append({"bar": t.bar_index, "side": side, "price": px, "type": "enter"})

    def _check_risk_and_checkpoints(self, t: ValidationTrial) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        paper = paper_metrics_from_trial(t)
        dd = paper["max_drawdown"]

        # automatic retirement conditions
        if dd >= self.max_drawdown_limit:
            t.status = TrialStatus.RETIRED
            t.verdict = TrialVerdict.RETIRE
            t.notes.append(f"retired: drawdown {dd:.2%} >= limit {self.max_drawdown_limit:.0%}")
            out["retired"] = "drawdown"
            return out
        if t.consecutive_losses >= self.max_consecutive_losses:
            t.status = TrialStatus.RETIRED
            t.verdict = TrialVerdict.RETIRE
            t.notes.append(f"retired: {t.consecutive_losses} consecutive losses")
            out["retired"] = "consecutive_losses"
            return out

        c30, c60, c90 = t.checkpoint_bars
        if t.bar_index >= c30 and "d30" not in t.checkpoints:
            t.checkpoints["d30"] = paper_metrics_from_trial(t)
            t.status = TrialStatus.CHECKPOINT_30
            t.verdict = TrialVerdict.CONTINUE
            out["checkpoint"] = "30d"
        if t.bar_index >= c60 and "d60" not in t.checkpoints:
            t.checkpoints["d60"] = paper_metrics_from_trial(t)
            t.status = TrialStatus.CHECKPOINT_60
            t.verdict = TrialVerdict.CONTINUE
            out["checkpoint"] = "60d"
        if t.bar_index >= c90 and "d90" not in t.checkpoints:
            t.checkpoints["d90"] = paper_metrics_from_trial(t)
            t.status = TrialStatus.CHECKPOINT_90
            out["checkpoint"] = "90d"
            self._finalize(t)
        return out

    def _finalize(self, t: ValidationTrial) -> None:
        paper = paper_metrics_from_trial(t)
        div = compute_divergence(t.backtest_metrics, paper)
        t.checkpoints["divergence"] = div.to_dict()
        t.checkpoints["final_paper"] = paper

        # independent evaluation
        if div.level == "HIGH" or paper["sharpe"] < self.min_paper_sharpe_90:
            t.verdict = TrialVerdict.RETIRE
            t.confidence = "low"
            t.notes.append(f"retire: divergence={div.level}, paper_sharpe={paper['sharpe']:.2f}")
        elif div.level == "LOW" and paper["sharpe"] >= self.min_paper_sharpe_90 and paper["max_drawdown"] < self.max_drawdown_limit:
            t.verdict = TrialVerdict.PROMOTE
            t.confidence = "moderate" if paper["sharpe"] < 0.8 else "high"
            t.notes.append("promote: low divergence and acceptable paper metrics")
        else:
            t.verdict = TrialVerdict.CONTINUE
            t.confidence = "moderate"
            t.notes.append("continue paper validation")

        t.status = TrialStatus.COMPLETE if t.verdict != TrialVerdict.RETIRE else TrialStatus.RETIRED

    def run_series(self, trial_id: str, series: Series) -> ValidationTrial:
        """Replay series bars through the trial (reproducible)."""
        self.start(trial_id)
        for b in series.bars:
            self.on_bar(trial_id, b.close, b.high, b.low)
            t = self.trials[trial_id]
            if t.status in (TrialStatus.COMPLETE, TrialStatus.RETIRED):
                break
        # if series shorter than 90d, still allow partial report
        t = self.trials[trial_id]
        if t.status == TrialStatus.RUNNING and t.bar_index >= t.checkpoint_bars[0]:
            # synthesize final evaluation on available data
            if "d90" not in t.checkpoints and t.bar_index >= t.checkpoint_bars[2]:
                pass
            elif t.bar_index >= t.checkpoint_bars[2] * 0.9:
                t.checkpoints.setdefault("d90", paper_metrics_from_trial(t))
                self._finalize(t)
        self._save_trial(t)
        return t

    def report(self, trial_id: str) -> str:
        t = self.trials[trial_id]
        paper = paper_metrics_from_trial(t)
        div = t.checkpoints.get("divergence") or compute_divergence(t.backtest_metrics, paper).to_dict()
        lines = [
            f"# Long-horizon validation report",
            f"Trial: {t.id}",
            f"Strategy fingerprint: {t.strategy_fingerprint} (LOCKED)",
            f"Symbol: {t.symbol} {t.timeframe}",
            f"Status: {t.status.value} | Verdict: {t.verdict.value} | Confidence: {t.confidence}",
            f"Bars processed: {t.bar_index} (~{t.day_count():.1f} days)",
            f"Execution: {t.execution}",
            "",
            "## Backtest baseline (pre-trial, frozen)",
            f"- Sharpe: {t.backtest_metrics.get('sharpe', 0):.2f}",
            f"- Drawdown: {t.backtest_metrics.get('max_drawdown', 0):.2%}",
            f"- Trades: {t.backtest_metrics.get('trades')}",
            f"- Return: {t.backtest_metrics.get('total_return', 0):.2%}",
            "",
            "## Paper metrics",
            f"- Sharpe: {paper.get('sharpe', 0):.2f}",
            f"- Drawdown: {paper.get('max_drawdown', 0):.2%}",
            f"- Trades: {int(paper.get('trades', 0))}",
            f"- Return: {paper.get('total_return', 0):.2%}",
            f"- Max consecutive losses: {int(paper.get('max_consecutive_losses', 0))}",
            "",
            "## Reality divergence",
            f"- Level: **{div.get('level')}**",
            f"- Sharpe gap (BT − Paper): {div.get('sharpe_gap', 0):.2f}",
            f"- Drawdown gap (Paper − BT): {div.get('drawdown_gap', 0):.2%}",
            f"- Trade count ratio: {div.get('trade_count_ratio', 0):.2f}",
            "",
            "## Checkpoints",
            f"- 30d: {t.checkpoints.get('d30')}",
            f"- 60d: {t.checkpoints.get('d60')}",
            f"- 90d: {t.checkpoints.get('d90')}",
            "",
            f"## Regime PnL\n{t.regime_pnl}",
            "",
            "## Notes",
            *[f"- {n}" for n in t.notes],
            "",
            "This is not a price forecast. Recommendation is based on historical",
            "paper divergence from the frozen backtest baseline only.",
            "No real-money orders. Candidate config was immutable during the trial.",
        ]
        return "\n".join(lines)
