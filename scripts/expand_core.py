#!/usr/bin/env python3
"""One-time: expand core/llm.py and core/memory.py from compressed payloads."""
import base64
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP = {
    "core/llm.py": "core/_llm.py.z64",
    "core/memory.py": "core/_memory.py.z64",
}

for dest_rel, src_rel in MAP.items():
    src = ROOT / src_rel
    dest = ROOT / dest_rel
    if not src.exists():
        print(f"missing {src_rel}")
        continue
    raw = src.read_text().strip().replace("\n", "")
    data = zlib.decompress(base64.b64decode(raw))
    dest.write_bytes(data)
    print(f"wrote {dest_rel} ({len(data)} bytes)")
print("done")
