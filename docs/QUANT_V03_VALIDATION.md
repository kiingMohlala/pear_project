# Quant v0.3 — Long-Horizon Paper Validation

## Purpose

Prove candidates can **survive time** without fooling themselves via overfitting.

```
Research → Robustness → Frozen Candidate
    → 30d paper → 60d paper → 90d paper
    → Independent evaluation → Promote / Continue / Retire
```

**Out of scope:** real money, capital allocation, self-modifying live strategies.

## Core rules

1. Strategy **fingerprint locked** for the trial  
2. Evaluation windows fixed at trial creation  
3. Execution costs: spread, commission, slippage, optional delay  
4. Primary metric: **backtest vs paper divergence**, not raw return  
5. Auto-retire on drawdown / consecutive losses / HIGH divergence  

## API

```python
from quant import LongHorizonValidator, parse_strategy, ExecutionModel
from quant.data import synthetic_ohlcv

val = LongHorizonValidator(execution=ExecutionModel(slippage_bps=1.0, delay_bars=1))
series = synthetic_ohlcv(n=400, seed=1)
# backtest window ≠ paper window
train, paper = series.bars[:200], series.bars[200:]
from quant.data import Series
train_s = Series(series.symbol, series.timeframe, train)
paper_s = Series(series.symbol, series.timeframe, paper)

strat = parse_strategy({"name": "sma_cross", "params": {"fast": 5, "slow": 20}})
trial = val.create_trial(strat, train_s, bars_per_day=1.0, checkpoint_days=(30, 60, 90))
val.run_series(trial.id, paper_s)
print(val.report(trial.id))
```

## Divergence levels

| Level | Typical meaning |
|-------|-----------------|
| LOW | Paper tracks backtest quality |
| MEDIUM | Some degradation — continue |
| HIGH | Collapse vs backtest — retire |
