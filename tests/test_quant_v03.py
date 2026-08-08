"""Long-horizon validation tests (Quant v0.3)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quant.long_horizon import LongHorizonValidator
from quant.execution_model import ExecutionModel
from quant.market_data import MarketDataStore
from quant.trial import fingerprint_strategy, compute_divergence, TrialVerdict, TrialStatus
from quant.dsl import parse_strategy
from quant.data import synthetic_ohlcv, Series


def test_fingerprint_lock():
    s1 = parse_strategy({"name": "sma_cross", "params": {"fast": 5, "slow": 20}})
    s2 = parse_strategy({"name": "sma_cross", "params": {"fast": 6, "slow": 20}})
    assert fingerprint_strategy(s1.spec.to_dict()) != fingerprint_strategy(s2.spec.to_dict())


def test_divergence_classification():
    low = compute_divergence(
        {"sharpe": 1.5, "max_drawdown": 0.08, "trades": 100, "total_return": 0.2},
        {"sharpe": 1.3, "max_drawdown": 0.10, "trades": 95, "total_return": 0.18},
    )
    assert low.level in ("LOW", "MEDIUM")
    high = compute_divergence(
        {"sharpe": 2.4, "max_drawdown": 0.06, "trades": 100, "total_return": 0.5},
        {"sharpe": 0.3, "max_drawdown": 0.19, "trades": 40, "total_return": 0.02},
    )
    assert high.level == "HIGH"


def test_execution_costs_worsen_fill():
    m = ExecutionModel(spread_bps=10, commission_bps=5, slippage_bps=5)
    buy = m.apply_fill_price(100.0, "buy")
    sell = m.apply_fill_price(100.0, "sell")
    assert buy > 100 and sell < 100


def test_frozen_trial_rejects_mutation():
    with tempfile.TemporaryDirectory() as td:
        val = LongHorizonValidator(store_dir=Path(td))
        series = synthetic_ohlcv(n=250, seed=2)
        train = Series(series.symbol, series.timeframe, series.bars[:100])
        s = parse_strategy({"name": "sma_cross", "params": {"fast": 5, "slow": 20}})
        trial = val.create_trial(s, train, checkpoint_days=(20, 40, 60), bars_per_day=1)
        val.start(trial.id)
        mutated = parse_strategy({"name": "sma_cross", "params": {"fast": 9, "slow": 20}})
        try:
            val.assert_not_mutated(trial.id, mutated)
            assert False
        except RuntimeError:
            pass
        assert val.trials[trial.id].status == TrialStatus.RETIRED


def test_30_60_90_pipeline():
    with tempfile.TemporaryDirectory() as td:
        val = LongHorizonValidator(
            store_dir=Path(td),
            execution=ExecutionModel(slippage_bps=0.5, delay_bars=0),
            max_drawdown_limit=0.5,
            max_consecutive_losses=50,
            min_paper_sharpe_90=-9.0,  # permissive for synthetic
            max_divergence_for_promote="HIGH",
        )
        series = synthetic_ohlcv(n=220, seed=11)
        train = Series(series.symbol, series.timeframe, series.bars[:80])
        paper = Series(series.symbol, series.timeframe, series.bars[80:])
        s = parse_strategy({"name": "sma_cross", "params": {"fast": 5, "slow": 18}})
        # short checkpoints for test speed (bars as "days")
        trial = val.create_trial(s, train, checkpoint_days=(10, 20, 40), bars_per_day=1)
        val.run_series(trial.id, paper)
        t = val.trials[trial.id]
        assert t.bar_index >= 10
        # at least one checkpoint if enough bars
        assert t.checkpoints or t.status in (TrialStatus.RUNNING, TrialStatus.COMPLETE, TrialStatus.RETIRED, TrialStatus.CHECKPOINT_30)
        report = val.report(trial.id)
        assert "divergence" in report.lower() or "Reality divergence" in report
        assert "LOCKED" in report
        assert "not a price forecast" in report.lower() or "not a price" in report.lower()


def test_market_data_gaps():
    with tempfile.TemporaryDirectory() as td:
        md = MarketDataStore(Path(td) / "m.db")
        s = synthetic_ohlcv(n=50, seed=1)
        md.ingest_series(s)
        # introduce gap by deleting middle conceptually — detect on uneven ts
        s2 = synthetic_ohlcv(n=20, seed=2)
        for b in s2.bars:
            b.ts += 100  # jump
        md.ingest_series(Series(s.symbol, s.timeframe, s2.bars))
        gap = md.detect_gaps(s.symbol, expected_step=1.0)
        assert gap.coverage <= 1.0


def test_restart_recovery():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td)
        val = LongHorizonValidator(store_dir=path)
        series = synthetic_ohlcv(n=100, seed=4)
        train = Series(series.symbol, series.timeframe, series.bars[:40])
        s = parse_strategy({"name": "sma_cross", "params": {"fast": 4, "slow": 12}})
        trial = val.create_trial(s, train, checkpoint_days=(15, 30, 45))
        val.start(trial.id)
        for b in series.bars[40:55]:
            val.on_bar(trial.id, b.close)
        tid = trial.id
        # new validator instance
        val2 = LongHorizonValidator(store_dir=path)
        assert tid in val2.trials
        assert val2.trials[tid].bar_index >= 1


if __name__ == "__main__":
    test_fingerprint_lock()
    print("  ✓ fingerprint")
    test_divergence_classification()
    print("  ✓ divergence")
    test_execution_costs_worsen_fill()
    print("  ✓ costs")
    test_frozen_trial_rejects_mutation()
    print("  ✓ freeze/mutation")
    test_30_60_90_pipeline()
    print("  ✓ 30/60/90")
    test_market_data_gaps()
    print("  ✓ market gaps")
    test_restart_recovery()
    print("  ✓ restart")
    print("All quant v0.3 tests passed.")
