# Quant v0.6 — Multi-Market Shadow Matrix

Compare a **frozen** candidate across markets, timeframes, and regimes.

## Ranking principle

Primary: **robustness score** (Sharpe, drawdown, trade count, profit factor).  
Raw return has only a weak weight. Minimum sample sizes required.

## Usage

```python
from quant import ShadowMatrix, parse_strategy
from quant.shadow_matrix import synthetic_universe_series

strat = parse_strategy({"name": "sma_cross", "params": {"fast": 5, "slow": 20}})
series_map = synthetic_universe_series(["BTCUSDT", "ETHUSDT", "EURUSD"], ["15m", "1h"], n=100)
matrix = ShadowMatrix()
result = matrix.run(strat, series_map)
print(result.report)
```

## Report sections

BEST CONDITIONS · BEST MARKETS · BEST TIMEFRAMES · WORST CONDITIONS  
ROBUSTNESS · SAMPLE SIZE · CONFIDENCE · KNOWN FAILURE MODES
