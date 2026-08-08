# Quant Lab v0.2 — Live Paper Trading Validation

## Safety
- **No real orders.** All adapters force `paper=True`.
- OANDA adapter only accepts practice hosts.
- IB adapter targets paper ports by convention.

## Lifecycle
`candidate → paper → pilot → production → retired`

Promotion uses configurable statistical/risk thresholds (`PromotionThresholds`).

## Engine
```python
from quant import PaperTradingEngine, parse_strategy, Stage
from quant.data import synthetic_ohlcv

eng = PaperTradingEngine()
s = parse_strategy({"name": "sma_cross", "params": {"fast": 5, "slow": 20}})
sid = eng.register(s, "SYN", stage=Stage.PAPER)
prices = [b.close for b in synthetic_ohlcv(n=150, seed=1).bars]
eng.run_price_path("SYN", prices)
print(eng.metrics(sid))
print(eng.promote_check(sid))
print(eng.validation_report(sid))
print(eng.dashboard_data())
```

## Storage
SQLite: signals, orders, fills, equity, quotes, strategy_state — reproducible path.

## Reports
- `validation_report(id, period="weekly"|"quarterly")`
- Dashboard data: active counts, stage breakdown, rankings
