# Quant v0.5 — Live Shadow-Market Validation

## Distinction

| Mode | Market path | Orders |
|------|-------------|--------|
| Paper | Simulated / replayed series | Virtual only |
| **Shadow** | **Actual live feed as it arrives** | **None — ever** |

## Safety

```
strategy → ShadowEngine → database
# NO path to broker trading API
# allows_real_orders = False
# broker = None
```

## Flow

```
LIVE MARKET → Feed (ts validate, gaps, dupes)
    → ShadowEngine (frozen candidates)
    → hypothetical signals + costed virtual fills
    → Shadow ledger (kind="shadow")
    → ResearchMemory
    → 30/60/90 report
```

## Usage

```python
from quant import ShadowEngine, parse_strategy
from quant.data import synthetic_ohlcv, Series

eng = ShadowEngine()
strat = parse_strategy({"name": "sma_cross", "params": {"fast": 5, "slow": 20}})
base = synthetic_ohlcv(n=100, seed=1)
trial = eng.start_trial(strat, "SYN", baseline_series=base, checkpoint_days=(10, 20, 40))
# live observer pushes:
for b in synthetic_ohlcv(n=50, seed=2).bars:
    eng.push_live_bar("SYN", b.close, ts=b.ts)
print(eng.report(trial.id))
print(eng.status())
```
