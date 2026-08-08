# Quant v0.7 — Evidence-Driven Hypothesis Generation

## Closed loop

```
observe → remember → hypothesize → test → falsify → validate → observe
```

Hypotheses **never** modify frozen paper/shadow candidates. They only spawn **new** research candidates that re-enter the full pipeline.

## Evidence rule

Reject: "Try RSI because it might work."  
Allow: proposals that cite parent experiments, failure patterns, and successful conditions.

## Pipeline (mandatory)

```
Hypothesis → Research candidate → research engine → robustness
  → paper → shadow → comparison
```

No shortcut based on backtest return alone.

## Usage

```python
from quant import ResearchLab, HypothesisEngine, parse_strategy
from quant.data import synthetic_ohlcv

lab = ResearchLab()
# ... accumulate experiments via lab.run_experiment ...
eng = HypothesisEngine(memory=lab.memory)
hyps = eng.generate_from_memory(family="sma")
print(hyps[0].human_readable())
exp = eng.evaluate_candidate_through_pipeline(hyps[0].id, synthetic_ohlcv(n=200, seed=1), research=lab)
print(eng.lineage_report(hyps[0].id))
```
