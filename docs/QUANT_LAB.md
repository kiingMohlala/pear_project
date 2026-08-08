# AI Quant Research Lab (Concept)

**Purpose:** Discover, evaluate, and evolve *rule-based* trading strategies using historical evidence.

**Not a price predictor.** Outputs are robustness assessments only.

## Modules

| Module | Role |
|--------|------|
| `quant/data.py` | OHLCV ingest, normalize, synthetic data |
| `quant/dsl.py` | Strategy DSL (entries/exits/indicators) |
| `quant/backtest.py` | Vector-ish bar backtester |
| `quant/evolve.py` | Mutation, crossover, population evolution |
| `quant/validate.py` | Walk-forward, OOS, Monte Carlo |
| `quant/optimize.py` | Multi-objective ranking / Pareto |
| `quant/regime.py` | Trend / range / volatility regimes |
| `quant/knowledge.py` | Strategy KB by market/timeframe |
| `quant/explain.py` | Human-readable assessment reports |
| `quant/engine.py` | `QuantResearchLab` orchestration |

## Quick start

```python
from quant import QuantResearchLab, parse_strategy

lab = QuantResearchLab()
series = lab.load_series(n=500, seed=42)  # synthetic offline
report = lab.research(series, population_size=30, generations=2)
print(report.disclaimer)
print(len(report.survivors), "survivors of", report.candidates_evaluated)

strat = parse_strategy({"name": "sma_cross", "params": {"fast": 10, "slow": 30}})
print(lab.explain(series, strat))
```

## Acceptance mapping

- Thousands of candidates → raise `population_size` × `generations`
- Reject non-robust → `evaluate_robustness(...).passed`
- Rank by quality → Sharpe/drawdown scalarization, not return alone
- Market/condition fit → KB `recommend_conditions` + regime summary
