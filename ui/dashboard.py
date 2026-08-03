"""
Minimal text-based dashboard / status view.
(Placeholder for a future web or rich TUI dashboard.)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.memory import Memory


def show_status(session_id: str = "cli") -> None:
    mem = Memory(session_id=session_id, persist_dir=ROOT / "data")
    print("── PEAR Status ──")
    print(f"Session        : {mem.session_id}")
    print(f"Messages       : {len(mem.working.messages)}")
    print(f"Notes          : {len(mem.knowledge.notes)}")
    if mem.working.messages:
        last = mem.working.messages[-1]
        print(f"Last message   : [{last.role}] {last.content[:80]}…")
    print("─────────────────")


if __name__ == "__main__":
    show_status()
