# Bayesian Optimization for Strategy Hyperparameters

## Role in the pipeline

```
Research (BO / evolution / grid)
        ↓
Historical objective scores
        ↓
Candidate params (FROZEN)
        ↓
Long-horizon paper validation (v0.3)
```

BO is a **research-side** search method. It must not retune a strategy while a paper trial is running.

## What it optimizes

Default: multi-objective **scalarized backtest score** (`return`, `drawdown`, `sharpe`, …)
over a parameter space (e.g. SMA `fast`/`slow`).

It does **not** optimize future P&amp;L and does not claim price prediction.

## Method

1. Random initial design  
2. Fit RBF Gaussian process on unit-scaled params  
3. Maximize Expected Improvement (EI)  
4. Evaluate backtest → update GP  

## Usage

```python
from quant.data import synthetic_ohlcv
from quant.dsl import parse_strategy
from quant.bayes_opt import optimize_strategy_params, compare_search_methods, ParamSpace

series = synthetic_ohlcv(n=300, seed=1)
base = parse_strategy({"name": "sma_cross", "params": {"fast": 10, "slow": 30}})
result = optimize_strategy_params(series, base, n_iter=20, seed=0)
print(result.best_params, result.best_score)
print(result.to_dict()["disclaimer"])

cmp = compare_search_methods(series, base, budget=15, seed=2)
print(cmp["random_best_score"], cmp["bo_best_score"])
```

## Custom space

```python
space = ParamSpace(bounds={
    "fast": (3, 25, True),   # int
    "slow": (15, 90, True),
})
```

## When to prefer BO vs evolution

| Method | Strength |
|--------|----------|
| Bayesian opt | Few continuous/integer params, expensive evals |
| Evolutionary pop | Discrete structure changes, large populations |
| Grid/random | Baselines and debugging |

After search: **freeze** params → `LongHorizonValidator.create_trial(...)`.
