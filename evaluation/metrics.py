"""
Simple evaluation metrics for PEAR agents.
"""

from __future__ import annotations

from typing import Any, Dict, List, Set


def risk_label_recall(detected: List[Dict[str, Any]], expected_labels: List[str]) -> float:
    """Fraction of expected risk labels that appear in detected risks."""
    if not expected_labels:
        return 1.0
    found: Set[str] = set()
    for r in detected:
        label = (r.get("label") or "").lower()
        for exp in expected_labels:
            if exp.lower() in label or label in exp.lower():
                found.add(exp)
    return len(found) / len(expected_labels)


def severity_count(detected: List[Dict[str, Any]], levels: List[str]) -> int:
    levels_l = {x.lower() for x in levels}
    return sum(1 for r in detected if (r.get("severity") or "").lower() in levels_l)


def contains_all(text: str, needles: List[str]) -> bool:
    lower = (text or "").lower()
    return all(n.lower() in lower for n in needles)


def score_review(
    *,
    risks: List[Dict[str, Any]],
    reply: str,
    expected_labels: List[str],
    min_high_or_critical: int = 0,
) -> Dict[str, Any]:
    recall = risk_label_recall(risks, expected_labels)
    high = severity_count(risks, ["high", "critical"])
    return {
        "label_recall": round(recall, 3),
        "high_or_critical": high,
        "meets_min_high": high >= min_high_or_critical,
        "reply_nonempty": bool((reply or "").strip()),
        "pass": recall >= 0.5 and high >= min_high_or_critical and bool((reply or "").strip()),
    }
