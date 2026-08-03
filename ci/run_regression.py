#!/usr/bin/env python3
"""Run core regression modules for CI (v3.00)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = [
    "tests/test_basic.py",
    "tests/test_ops_v240.py",
    "tests/test_workers_v230.py",
    "tests/test_service_v220.py",
    "tests/test_goals_v200.py",
    "tests/test_learning_v210.py",
    "tests/test_n8n_v235.py",
    "tests/test_e2e_v300.py",
    "tests/test_perf_v300.py",
]


def main() -> int:
    failed = []
    for t in TESTS:
        path = ROOT / t
        if not path.exists():
            print(f"SKIP {t}")
            continue
        print(f"RUN  {t}")
        r = subprocess.run([sys.executable, str(path)], cwd=str(ROOT))
        if r.returncode != 0:
            failed.append(t)
            print(f"FAIL {t}")
        else:
            print(f"PASS {t}")
    print("---")
    if failed:
        print("FAILED:", ", ".join(failed))
        return 1
    print("All regression suites passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
