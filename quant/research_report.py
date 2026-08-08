"""Human-readable research reports — never claim future profitability."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .experiment import ExperimentRecord
from .analysis import (
    backtest_oos_degradation,
    backtest_paper_divergence,
    classify_failure,
    parameter_stability,
)
from .research_memory import ResearchMemory


DISCLAIMER = (
    "This report summarizes historical experiments only. "
    "It does not predict future prices or guarantee live profitability."
)


def generate_report(
    exp: ExperimentRecord,
    memory: Optional[ResearchMemory] = None,
) -> str:
    deg = backtest_oos_degradation(exp)
    div = backtest_paper_divergence(exp)
    failures = classify_failure(exp)
    similar = []
    if memory is not None:
        similar = memory.similar_experiments(
            family=exp.strategy_family,
            market=exp.market,
            limit=5,
        )
        similar = [s for s in similar if s.id != exp.id]

    lines = [
        f"# Research report: {exp.strategy_name}",
        f"Experiment: `{exp.id}`",
        f"Family: {exp.strategy_family} | Market: {exp.market} {exp.timeframe}",
        f"Fingerprint: `{exp.strategy_fingerprint}` | Sealed: {exp.sealed}",
        f"Disposition: **{exp.disposition.value}**",
        "",
        f"_{DISCLAIMER}_",
        "",
        "## Parameters",
        f"`{exp.parameters}`",
        "",
        "## Backtest",
        f"- Sharpe: {exp.backtest.get('sharpe', 'n/a')}",
        f"- Drawdown: {exp.backtest.get('max_drawdown', 'n/a')}",
        f"- Trades: {exp.backtest.get('trades', 'n/a')}",
        f"- Return: {exp.backtest.get('total_return', 'n/a')}",
        "",
        "## Out-of-sample / walk-forward",
        f"- OOS metrics: {exp.oos or 'n/a'}",
        f"- Sharpe degradation (BT→OOS): {deg['sharpe_degradation']:.3f}",
        f"- Unstable OOS: {deg['unstable']}",
        "",
        "## Monte Carlo",
        f"{exp.monte_carlo or 'n/a'}",
        "",
        "## Paper (if any)",
        f"{exp.paper or 'n/a'}",
        "",
        "## Backtest vs paper divergence",
        f"- Level: **{div.get('level')}**",
        f"- Sharpe gap: {div.get('sharpe_gap')}",
        f"- Drawdown gap: {div.get('drawdown_gap')}",
        "",
        "## Regimes",
        f"{exp.regimes or 'n/a'}",
        "",
        "## Failure / caution tags",
        *(f"- {r}" for r in (failures or ["none flagged"])),
        "",
        "## Comparable experiments",
    ]
    if not similar:
        lines.append("- none in memory")
    else:
        for s in similar:
            lines.append(
                f"- {s.id} [{s.disposition.value}] {s.market} {s.timeframe} "
                f"bt_sharpe={s.backtest.get('sharpe')}"
            )
    lines += ["", "## Notes", *(f"- {n}" for n in (exp.notes or ["—"]))]
    return "\n".join(lines)


def family_insight(family: str, memory: ResearchMemory) -> str:
    perf = memory.family_performance(family)
    best = memory.best_conditions(family)
    fails = memory.failure_patterns()
    lines = [
        f"# Family insight: {family}",
        f"_{DISCLAIMER}_",
        "",
        f"Experiments: {perf.get('n', 0)}",
        f"Dispositions: {perf.get('dispositions')}",
        f"Markets tested: {perf.get('markets')}",
        "",
        "## Best observed conditions (historical)",
    ]
    if not best:
        lines.append("- insufficient survivors")
    else:
        for b in best[:5]:
            lines.append(
                f"- {b['market']} {b['timeframe']} sharpe={b['sharpe']:.2f} "
                f"div={b.get('divergence')} [{b['disposition']}]"
            )
    lines.append("")
    lines.append("## Recurring failures (global memory)")
    for f in fails[:5]:
        lines.append(f"- {f['reason']} (n={f['count']})")
    return "\n".join(lines)
