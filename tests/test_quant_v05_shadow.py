"""Shadow-market validation tests (Quant v0.5)."""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quant.shadow_engine import ShadowEngine
from quant.shadow_market import ShadowMarketFeed
from quant.shadow_trade import TRADE_KIND
from quant.dsl import parse_strategy
from quant.data import synthetic_ohlcv, Bar, Series
from quant.execution_model import ExecutionModel
from quant.research_lab import ResearchLab
from quant.trial import fingerprint_strategy


def test_no_broker_and_kind():
    eng = ShadowEngine()
    assert eng.allows_real_orders is False
    assert eng.broker is None
    assert TRADE_KIND == "shadow"


def test_feed_rejects_future_and_duplicate():
    feed = ShadowMarketFeed(expected_step=1.0, max_future_skew_s=1.0)
    ok, _ = feed.push_price("SYN", 100.0, ts=time.time())
    assert ok
    ok2, reason = feed.push_price("SYN", 100.1, ts=time.time() + 100)
    assert not ok2 and reason == "future_timestamp"
    ts = time.time() - 10
    feed2 = ShadowMarketFeed()
    feed2.push_price("SYN", 1.0, ts=ts)
    ok3, reason3 = feed2.push_price("SYN", 1.0, ts=ts)
    assert not ok3 and reason3 == "duplicate"


def test_shadow_trial_runs_and_reports():
    with tempfile.TemporaryDirectory() as td:
        research = ResearchLab(memory_path=Path(td) / "mem.json")
        eng = ShadowEngine(
            research=research,
            persist_dir=Path(td) / "shadow",
            execution=ExecutionModel(delay_bars=0),
            max_drawdown_limit=0.9,
        )
        strat = parse_strategy({"name": "sma_cross", "params": {"fast": 4, "slow": 12}})
        base = synthetic_ohlcv(n=80, seed=1)
        trial = eng.start_trial(
            strat, "SYN",
            baseline_series=base,
            checkpoint_days=(8, 16, 30),
            bars_per_day=1.0,
        )
        live = synthetic_ohlcv(n=40, seed=2)
        for i, b in enumerate(live.bars):
            # monotonic ts in the past
            eng.push_live_bar("SYN", b.close, ts=1_700_000_000 + i)
        st = eng.status(trial.id)
        assert st["allows_real_orders"] is False
        assert st["broker"] is None
        report = eng.report(trial.id)
        assert "Shadow-market" in report
        assert "False" in report
        t = eng.trials[trial.id]
        assert all(s.get("kind") == "shadow" for s in t.signals) or len(t.signals) == 0
        for tr in t.trades_log:
            assert tr.get("kind") == "shadow"


def test_mutation_retires():
    with tempfile.TemporaryDirectory() as td:
        eng = ShadowEngine(persist_dir=Path(td))
        s = parse_strategy({"name": "sma_cross", "params": {"fast": 5, "slow": 20}})
        trial = eng.start_trial(s, "SYN", checkpoint_days=(5, 10, 15))
        bad = parse_strategy({"name": "sma_cross", "params": {"fast": 9, "slow": 20}})
        try:
            eng.assert_fingerprint(trial.id, bad)
            assert False
        except RuntimeError:
            pass
        assert eng.trials[trial.id].status.value == "retired"


def test_out_of_order_rejected():
    feed = ShadowMarketFeed()
    feed.push_price("X", 1.0, ts=100.0)
    ok, reason = feed.push_price("X", 1.1, ts=99.0)
    assert not ok and reason == "out_of_order"


def test_server_ts_on_signals():
    with tempfile.TemporaryDirectory() as td:
        eng = ShadowEngine(persist_dir=Path(td), max_drawdown_limit=0.9)
        s = parse_strategy({"name": "sma_cross", "params": {"fast": 3, "slow": 8}})
        trial = eng.start_trial(s, "SYN", checkpoint_days=(20, 40, 60))
        for i in range(25):
            eng.on_bar(trial.id, Bar(ts=1_000.0 + i, open=100+i*0.01, high=100+i*0.01, low=100+i*0.01, close=100+i*0.01))
        for sig in eng.trials[trial.id].signals:
            assert "server_ts" in sig
            assert sig["server_ts"] > 0


if __name__ == "__main__":
    test_no_broker_and_kind()
    print("  ✓ no broker")
    test_feed_rejects_future_and_duplicate()
    print("  ✓ feed validation")
    test_out_of_order_rejected()
    print("  ✓ out of order")
    test_shadow_trial_runs_and_reports()
    print("  ✓ shadow run")
    test_mutation_retires()
    print("  ✓ mutation")
    test_server_ts_on_signals()
    print("  ✓ server_ts")
    print("All quant v0.5 shadow tests passed.")
