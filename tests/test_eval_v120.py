"""Evaluation engine regression tests (v1.20)."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.engine import EvaluationEngine, SuiteReport, CaseResult


def test_list_suites():
    eng = EvaluationEngine()
    suites = eng.list_suites()
    for required in ("planner", "retrieval", "legal", "finance", "workflow"):
        assert required in suites


def test_run_planner_suite_deterministic():
    with tempfile.TemporaryDirectory() as td:
        eng = EvaluationEngine(history_dir=Path(td) / "h", baseline_dir=Path(td) / "b")
        r1 = eng.run(suites=["planner"], save_history=True, compare_baseline=False)
        r2 = eng.run(suites=["planner"], save_history=True, compare_baseline=False)
        assert r1.suites["planner"].success_rate == r2.suites["planner"].success_rate
        assert r1.metrics["success_rate"] == r2.metrics["success_rate"]


def test_history_and_baseline():
    with tempfile.TemporaryDirectory() as td:
        eng = EvaluationEngine(history_dir=Path(td) / "h", baseline_dir=Path(td) / "b")
        report = eng.run(suites=["planner", "media"], save_history=True, compare_baseline=False)
        eng.save_baseline(report)
        hist = eng.history()
        assert any(h["id"] == report.id for h in hist)
        report2 = eng.run(suites=["planner", "media"], save_history=True, compare_baseline=True)
        assert report2.baseline_comparison is not None
        assert report2.baseline_comparison.get("status") in ("ok", "regressed", "no_baseline")


def test_compare_builds():
    with tempfile.TemporaryDirectory() as td:
        eng = EvaluationEngine(history_dir=Path(td) / "h", baseline_dir=Path(td) / "b")
        a = eng.run(suites=["planner"], save_history=True, compare_baseline=False)
        b = eng.run(suites=["planner"], save_history=True, compare_baseline=False)
        cmp = eng.compare_builds(a.id, b.id)
        assert cmp.get("ok")


def test_export_csv():
    with tempfile.TemporaryDirectory() as td:
        eng = EvaluationEngine(history_dir=Path(td) / "h", baseline_dir=Path(td) / "b")
        report = eng.run(suites=["media"], save_history=False, compare_baseline=False)
        path = eng.export_csv(report, Path(td) / "out.csv")
        assert path.exists()
        text = path.read_text()
        assert "suite" in text and "media" in text


def test_quality_report_string():
    with tempfile.TemporaryDirectory() as td:
        eng = EvaluationEngine(history_dir=Path(td) / "h", baseline_dir=Path(td) / "b")
        report = eng.run(suites=["plugins"], save_history=True, compare_baseline=False)
        text = eng.quality_report(report)
        assert "Quality Report" in text
        assert "plugins" in text


def test_isolation_temp_state():
    """Evaluation should not require or corrupt global session data."""
    with tempfile.TemporaryDirectory() as td:
        eng = EvaluationEngine(history_dir=Path(td) / "h", baseline_dir=Path(td) / "b")
        report = eng.run(suites=["desktop", "retrieval"], save_history=True, compare_baseline=False)
        assert report.metrics.get("total_cases", 0) >= 2
        # only wrote under td
        assert list((Path(td) / "h").glob("*.json"))


if __name__ == "__main__":
    test_list_suites()
    print("  ✓ suites")
    test_run_planner_suite_deterministic()
    print("  ✓ deterministic")
    test_history_and_baseline()
    print("  ✓ history/baseline")
    test_compare_builds()
    print("  ✓ compare")
    test_export_csv()
    print("  ✓ csv")
    test_quality_report_string()
    print("  ✓ quality report")
    test_isolation_temp_state()
    print("  ✓ isolation")
    print("All v1.20 evaluation tests passed.")
