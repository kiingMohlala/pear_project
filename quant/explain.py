"""Explainability reports — why a strategy looked strong or weak historically."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .backtest import BacktestResult
from .validate import RobustnessReport


def explain_result(result: BacktestResult, robust: Optional[RobustnessReport] = None) -> str:
    lines = [
        f"# Strategy assessment: {result.strategy_name}",
        "",
        "**Disclaimer:** This is an evidence-based review of *historical* behaviour.",
        "It does **not** predict future prices or guarantee live performance.",
        "",
        "## Snapshot",
        f"- Market: {result.symbol} ({result.timeframe})",
        f"- Bars: {result.n_bars}, Trades: {result.trades}",
        f"- Total return (hist.): {result.total_return:.2%}",
        f"- Max drawdown: {result.max_drawdown:.2%}",
        f"- Win rate: {result.win_rate:.2%}",
        f"- Sharpe (approx.): {result.sharpe:.2f}",
        f"- Profit factor: {result.profit_factor:.2f}",
        f"- Params: {result.params}",
        "",
    ]
    # qualitative
    lines.append("## Interpretation")
    if result.trades < 5:
        lines.append("- Too few trades for stable inference; treat metrics as anecdotal.")
    if result.max_drawdown > 0.25:
        lines.append("- Large historical drawdowns reduce robustness confidence.")
    if result.sharpe > 1.0 and result.max_drawdown < 0.2:
        lines.append("- Risk-adjusted historical profile is comparatively favourable.")
    elif result.total_return > 0 and result.sharpe < 0.3:
        lines.append("- Positive return but weak risk-adjusted quality — may be path-lucky.")
    if result.win_rate < 0.4 and result.profit_factor > 1.2:
        lines.append("- Low win rate with higher profit factor suggests asymmetric winners.")
    if robust:
        lines.append("")
        lines.append("## Robustness")
        lines.append(f"- Passed gates: **{robust.passed}**")
        lines.append(f"- Walk-forward mean return: {robust.walk_forward_mean_return:.2%}")
        lines.append(f"- Walk-forward mean Sharpe: {robust.walk_forward_mean_sharpe:.2f}")
        lines.append(f"- Out-of-sample return: {robust.oos_return:.2%}, Sharpe: {robust.oos_sharpe:.2f}")
        lines.append(f"- Monte Carlo median / p5 return: {robust.monte_carlo_median_return:.2%} / {robust.monte_carlo_p5_return:.2%}")
        if robust.reasons:
            lines.append("- Rejection / caution reasons:")
            for r in robust.reasons:
                lines.append(f"  - {r}")
    lines.append("")
    lines.append("## Suitability")
    lines.append("Prefer this strategy only where similar regimes and liquidity conditions")
    lines.append("matched the evaluation set — and only after independent review.")
    return "\n".join(lines)
