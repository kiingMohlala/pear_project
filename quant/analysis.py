"""Cross-experiment analysis: degradation, divergence, stability, failures."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .experiment import ExperimentRecord


def backtest_oos_degradation(exp: ExperimentRecord) -> Dict[str, Any]:
    bt_s = float(exp.backtest.get("sharpe") or 0)
    oos_s = float(exp.oos.get("sharpe") or 0)
    bt_dd = float(exp.backtest.get("max_drawdown") or 0)
    oos_dd = float(exp.oos.get("max_drawdown") or 0)
    return {
        "sharpe_degradation": bt_s - oos_s,
        "drawdown_increase": oos_dd - bt_dd,
        "unstable": (bt_s - oos_s) > 0.8 or (oos_dd - bt_dd) > 0.08,
    }


def backtest_paper_divergence(exp: ExperimentRecord) -> Dict[str, Any]:
    if exp.divergence:
        return dict(exp.divergence)
    bt_s = float(exp.backtest.get("sharpe") or 0)
    p_s = float(exp.paper.get("sharpe") or 0)
    bt_dd = float(exp.backtest.get("max_drawdown") or 0)
    p_dd = float(exp.paper.get("max_drawdown") or 0)
    gap = bt_s - p_s
    dd_gap = p_dd - bt_dd
    if gap > 1.0 or dd_gap > 0.10 or (bt_s > 0.5 and p_s < 0.3 * bt_s):
        level = "HIGH"
    elif gap > 0.4 or dd_gap > 0.05:
        level = "MEDIUM"
    else:
        level = "LOW"
    return {
        "sharpe_gap": gap,
        "drawdown_gap": dd_gap,
        "level": level,
        "backtest_sharpe": bt_s,
        "paper_sharpe": p_s,
    }


def regime_performance(exp: ExperimentRecord) -> Dict[str, Any]:
    return dict(exp.regimes or {})


def parameter_stability(experiments: List[ExperimentRecord]) -> Dict[str, Any]:
    """Across related experiments, are best params tightly clustered?"""
    if not experiments:
        return {"n": 0, "stable": True}
    param_keys = set()
    for e in experiments:
        param_keys |= set(e.parameters.keys())
    spread = {}
    for k in param_keys:
        vals = [float(e.parameters[k]) for e in experiments if k in e.parameters]
        if len(vals) < 2:
            spread[k] = {"min": vals[0] if vals else None, "max": vals[0] if vals else None, "range": 0}
        else:
            spread[k] = {"min": min(vals), "max": max(vals), "range": max(vals) - min(vals)}
    # crude: unstable if any param range is large relative to magnitude
    unstable = any(
        (v["range"] or 0) > max(5.0, abs(v["min"] or 0) * 0.5)
        for v in spread.values()
    )
    return {"n": len(experiments), "param_spread": spread, "stable": not unstable}


def execution_sensitivity(exp: ExperimentRecord) -> Dict[str, Any]:
    return {
        "execution": exp.execution,
        "note": "Higher costs/delays typically reduce paper vs backtest alignment",
        "divergence_level": (exp.divergence or {}).get("level"),
    }


def classify_failure(exp: ExperimentRecord) -> List[str]:
    reasons = list(exp.failure_reasons)
    deg = backtest_oos_degradation(exp)
    if deg.get("unstable"):
        reasons.append("oos_degradation")
    div = backtest_paper_divergence(exp)
    if div.get("level") == "HIGH":
        reasons.append("high_paper_divergence")
    if float(exp.backtest.get("trades") or 0) < 5:
        reasons.append("insufficient_trades")
    if float(exp.backtest.get("max_drawdown") or 0) > 0.35:
        reasons.append("excessive_backtest_drawdown")
    # dedupe
    seen = set()
    out = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out
