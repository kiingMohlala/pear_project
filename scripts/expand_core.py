#!/usr/bin/env python3
"""Expand compressed core modules (one-time setup if needed)."""
import base64, zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = {
    "core/llm.py": ROOT / "core" / "_llm.py.z64",
    "core/memory.py": ROOT / "core" / "_memory.py.z64",
}

for dest, src in FILES.items():
    if not src.exists():
        print(f"skip {dest}: missing {src.name}")
        continue
    out = ROOT / dest
    if out.exists() and out.stat().st_size > 100:
        print(f"ok  {dest} already present")
        continue
    data = zlib.decompress(base64.b64decode(src.read_text().strip()))
    out.write_bytes(data)
    print(f"wrote {dest} ({len(data)} bytes)")
