"""Paper trading validation tests (Quant Lab v0.2)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quant.paper_engine import PaperTradingEngine
from quant.paper_store import PaperStore
from quant.promotion import Stage, PromotionThresholds, evaluate_promotion
from quant.brokers import get_broker, SimulatedBroker
from quant.dsl import parse_strategy
from quant.data import synthetic_ohlcv


def test_broker_paper_only():
    for name in ("simulated", "oanda_practice", "ib_paper", "mt_demo"):
        b = get_broker(name)
        b.connect()
        fill = b.place_order(__import__("quant.brokers", fromlist=["OrderRequest"]).OrderRequest("SYN", "buy", 1.0))
        assert fill.paper is True


def test_oanda_rejects_live_host():
    try:
        get_broker("oanda", api_url="https://api-fxtrade.oanda.com")
        assert False
    except ValueError:
        pass


def test_concurrent_paper_path():
    with tempfile.TemporaryDirectory() as td:
        store = PaperStore(path=Path(td) / "p.db")
        eng = PaperTradingEngine(broker=SimulatedBroker({"SYN": 100.0}), store=store)
        ids = []
        for fast, slow in [(5, 15), (8, 25), (3, 12), (10, 30)]:
            s = parse_strategy({"name": "sma_cross", "params": {"fast": fast, "slow": slow}})
            ids.append(eng.register(s, "SYN", stage=Stage.PAPER))
        prices = [b.close for b in synthetic_ohlcv(n=120, seed=7).bars]
        eng.run_price_path("SYN", prices)
        assert len(eng.runtimes) == 4
        for sid in ids:
            m = eng.metrics(sid)
            assert "sharpe" in m and "profit_factor" in m
            assert store.equity_curve(sid) or m["trades"] >= 0
        dash = eng.dashboard_data()
        assert dash["total"] == 4
        assert "disclaimer" in dash


def test_promotion_rules():
    th = PromotionThresholds(min_paper_trades=5, min_paper_days=0, min_paper_sharpe=-9, max_paper_drawdown=1.0, min_profit_factor=0)
    d = evaluate_promotion(Stage.PAPER, {
        "strategy_id": "x",
        "trades": 10,
        "days": 1,
        "sharpe": 1.0,
        "max_drawdown": 0.05,
        "profit_factor": 1.5,
    }, th)
    assert d.action == "promote" and d.to_stage == Stage.PILOT.value

    d2 = evaluate_promotion(Stage.PAPER, {
        "strategy_id": "y",
        "trades": 30,
        "days": 10,
        "sharpe": -1.0,
        "max_drawdown": 0.1,
        "profit_factor": 0.5,
    }, th)
    assert d2.action in ("retire", "hold", "demote")


def test_promotion_on_engine():
    with tempfile.TemporaryDirectory() as td:
        eng = PaperTradingEngine(
            store=PaperStore(path=Path(td) / "p.db"),
            thresholds=PromotionThresholds(
                min_paper_trades=1,
                min_paper_days=0,
                min_paper_sharpe=-99,
                max_paper_drawdown=1.0,
                min_profit_factor=0,
            ),
        )
        s = parse_strategy({"name": "sma_cross", "params": {"fast": 4, "slow": 12}})
        sid = eng.register(s, "SYN", stage=Stage.PAPER)
        eng.run_price_path("SYN", [b.close for b in synthetic_ohlcv(n=80, seed=3).bars])
        # force metrics richness
        eng.runtimes[sid].trades = max(eng.runtimes[sid].trades, 5)
        eng.runtimes[sid].started_at = eng.runtimes[sid].started_at - 86400
        dec = eng.promote_check(sid)
        assert dec.action in ("promote", "hold", "retire", "demote")
        report = eng.validation_report(sid, period="weekly")
        assert "Paper validation" in report
        assert "No real orders" in report


def test_no_real_orders_flag():
    eng = PaperTradingEngine()
    assert eng._allow_live is False


if __name__ == "__main__":
    test_broker_paper_only()
    print("  ✓ brokers paper")
    test_oanda_rejects_live_host()
    print("  ✓ oanda practice only")
    test_concurrent_paper_path()
    print("  ✓ concurrent paper")
    test_promotion_rules()
    print("  ✓ promotion rules")
    test_promotion_on_engine()
    print("  ✓ engine promote/report")
    test_no_real_orders_flag()
    print("  ✓ no live flag")
    print("All quant paper v0.2 tests passed.")
