#!/usr/bin/env python3
"""Run full evaluation and optionally save as baseline. For jobs/cron."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from evaluation.engine import EvaluationEngine

def main():
    eng = EvaluationEngine()
    report = eng.run(save_history=True, compare_baseline=True)
    print(eng.quality_report(report))
    csv_path = eng.history_dir / f"{report.id}.csv"
    eng.export_csv(report, csv_path)
    print(f"csv={csv_path}")
    if report.baseline_comparison and report.baseline_comparison.get("status") == "regressed":
        sys.exit(2)
    sys.exit(0)

if __name__ == "__main__":
    main()
