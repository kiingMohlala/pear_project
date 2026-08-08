# Quant v0.8 — Independent Research Review

## Goal
Decide which hypotheses are worth continuing **without** ranking by raw return and **without** leaking research-optimization data into final evaluation.

## Layers
1. `IndependentValidator` — disjoint data only; blocks timestamp overlap
2. `CandidateScorecard` — multi-dimensional score + sample-aware confidence
3. `ResearchDecision` — PROMOTE_TO_LONG_HORIZON_VALIDATION | CONTINUE | RETEST | FALSIFIED | RETIRE | INSUFFICIENT_EVIDENCE
4. `ResearchReviewBoard` — package + lineage queries

## Decisions
Never include automatic real-money promotion.
