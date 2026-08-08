"""Multi-market shadow matrix tests (Quant v0.6)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quant.dsl import parse_strategy
from quant.shadow_matrix import ShadowMatrix, synthetic_universe_series
from quant.universe import build_matrix, MarketSpec, TimeframeSpec
from quant.comparison import robustness_score, rank_cells, CellResult, comparative_report
from quant.regime_analysis import score_regime_bucket, aggregate_regime_pnl
from quant.trial import fingerprint_strategy
from quant.research_lab import ResearchLab


def test_universe_matrix_size():
    cells = build_matrix(
        [("abc", "sma")],
        markets=[MarketSpec("A"), MarketSpec("B")],
        timeframes=[TimeframeSpec("1h", 24), TimeframeSpec("1d", 1)],
    )
    assert len(cells) == 4


def test_robustness_penalizes_high_divergence():
    m = {"sharpe": 1.0, "max_drawdown": 0.1, "trades": 20, "profit_factor": 1.2, "total_return": 0.1}
    low = robustness_score(m, "LOW")
    high = robustness_score(m, "HIGH")
    assert low > high


def test_rank_not_by_return_alone():
    a = CellResult("f", "s", "M1", "1h", {"sharpe": 0.2, "max_drawdown": 0.05, "trades": 20, "profit_factor": 1.1, "total_return": 0.5}, "LOW", trades=20)
    b = CellResult("f", "s", "M2", "1h", {"sharpe": 1.2, "max_drawdown": 0.08, "trades": 20, "profit_factor": 1.4, "total_return": 0.1}, "LOW", trades=20)
    ranked = rank_cells([a, b], min_trades=5)
    assert ranked[0].market == "M2"  # better risk-adjusted wins over higher raw return


def test_matrix_run_frozen():
    with tempfile.TemporaryDirectory() as td:
        research = ResearchLab(memory_path=Path(td) / "mem.json")
        matrix = ShadowMatrix(research=research, persist_dir=Path(td) / "mx")
        strat = parse_strategy({"name": "sma_cross", "params": {"fast": 4, "slow": 12}})
        fp = fingerprint_strategy(strat.spec.to_dict())
        series_map = synthetic_universe_series(["BTCUSDT", "EURUSD"], ["1h", "1d"], n=80, seed=7)
        result = matrix.run(strat, series_map)
        assert len(result.cells) == 4
        assert all(c.fingerprint == fp for c in result.cells)
        assert "BEST" in result.report
        assert "raw return" in result.report.lower() or "risk-adjusted" in result.report.lower()
        assert "No capital allocation" in result.report


def test_regime_bucket_sample_gate():
    assert score_regime_bucket({"n": 2, "avg_pnl": 0.05, "win_rate": 0.9}, min_n=5) == "insufficient_sample"
    assert score_regime_bucket({"n": 10, "avg_pnl": 0.02, "win_rate": 0.5}, min_n=5) == "strong"


def test_comparative_insufficient_sample_message():
    cells = [
        CellResult("f", "s", "M", "1h", {"sharpe": 1, "max_drawdown": 0.1, "trades": 1, "profit_factor": 1, "total_return": 0.01}, trades=1),
    ]
    text = comparative_report(cells, min_trades=5)
    assert "insufficient" in text.lower()


if __name__ == "__main__":
    test_universe_matrix_size()
    print("  ✓ universe")
    test_robustness_penalizes_high_divergence()
    print("  ✓ score")
    test_rank_not_by_return_alone()
    print("  ✓ rank")
    test_regime_bucket_sample_gate()
    print("  ✓ regime gate")
    test_comparative_insufficient_sample_message()
    print("  ✓ sample msg")
    test_matrix_run_frozen()
    print("  ✓ matrix run")
    print("All quant v0.6 matrix tests passed.")
