# Quant v0.4 — Research Intelligence & Experiment Memory

## Purpose

Learn from **past experiments** without modifying strategies during evaluation.

```
Idea → candidates → gates → freeze → paper → independent review
                              ↑
                     Research Memory (immutable records)
```

## Components

| Module | Role |
|--------|------|
| `experiment.py` | Immutable sealed records + content hash |
| `research_memory.py` | Persist/search/pattern aggregation |
| `analysis.py` | Degradation, divergence, stability, failures |
| `research_report.py` | Human reports (no profitability claims) |
| `research_lab.py` | Facade + queries |

## Queries

```python
from quant import ResearchLab, parse_strategy
from quant.data import synthetic_ohlcv

lab = ResearchLab()
series = synthetic_ohlcv(n=250, seed=1)
s = parse_strategy({"name": "sma_cross", "params": {"fast": 5, "slow": 20}})
exp = lab.run_experiment(s, series)
print(lab.report(exp.id))
print(lab.best_conditions("sma"))
print(lab.failure_patterns())
print(lab.market_summary("SYN"))
print(lab.research_history())
```

## Explicitly out of scope

Real money · capital allocation · self-modifying live strategies · broker live credentials · auto real promotion
