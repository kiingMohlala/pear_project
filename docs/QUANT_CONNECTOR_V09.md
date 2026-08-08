# Quant v0.9 — PEAR Quant Connector

## Principle
PEAR orchestrates. Quant researches. **No real orders through PEAR.**

```
PEAR Planner / Jobs / Workflows
        ↓
ConnectorRegistry["quant"]
        ↓
Quant Research Lab (independent package)
```

## Actions
`quant_research` · `quant_status` · `quant_candidates` · `quant_hypotheses` · `quant_review` · `quant_report` · `quant_market_summary` · `quant_failure_patterns` · `quant_lineage` · `quant_shadow_status`

## Forbidden
place_order · buy/sell · allocate · broker credentials · modify frozen models

## Workflow
```python
WorkflowStep(type=StepType.CONNECTOR, connector="quant",
             connector_action="quant_research",
             connector_params={"name": "sma_cross", "symbol": "BTCUSDT"})
```

## Jobs
Wrap `reg.execute("quant", "quant_research", ...)` in PEAR JobManager for long runs.
