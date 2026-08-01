"""
Shared tools available to agents.
"""

from __future__ import annotations

import os
import platform
import subprocess
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── File Reading ──────────────────────────────────────────────────

def read_pdf(path: str | Path) -> str:
    """Extract text from a PDF. Tries pypdf, falls back to pdfminer if available."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            pages.append(f"--- Page {i + 1} ---\n{text}")
        return "\n\n".join(pages).strip()
    except ImportError:
        pass

    try:
        from pdfminer.high_level import extract_text
        return extract_text(str(path)).strip()
    except ImportError:
        raise RuntimeError(
            "No PDF library found. Install with: pip install pypdf  (or pdfminer.six)"
        )


def read_docx(path: str | Path) -> str:
    """Extract text from a DOCX file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"DOCX not found: {path}")

    try:
        from docx import Document
        doc = Document(str(path))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs).strip()
    except ImportError:
        raise RuntimeError(
            "python-docx not found. Install with: pip install python-docx"
        )


def read_document(path: str | Path) -> str:
    """Auto-detect PDF or DOCX and extract text."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return read_pdf(path)
    if suffix in {".docx", ".doc"}:
        return read_docx(path)
    raise ValueError(f"Unsupported file type: {suffix}. Use .pdf or .docx")


def summarize_text(text: str, max_chars: int = 1500) -> str:
    """
    Lightweight extractive summary (no external LLM required).
    Takes first N characters of meaningful paragraphs + a note about length.
    In production this would call an LLM; for v0.1 we keep it local.
    """
    if not text or not text.strip():
        return "Document appears empty."

    clean = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(clean) <= max_chars:
        return clean

    # Prefer paragraph boundaries
    truncated = clean[:max_chars]
    last_break = max(truncated.rfind("\n\n"), truncated.rfind(". "))
    if last_break > max_chars // 2:
        truncated = truncated[: last_break + 1]

    return (
        truncated.strip()
        + f"\n\n[... truncated – original length ~{len(clean)} chars. "
        "Full text available via memory or re-upload.]"
    )


# ── Desktop / OS Tasks ────────────────────────────────────────────

def _is_windows() -> bool:
    return platform.system() == "Windows"


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def open_application(name: str) -> Dict[str, Any]:
    """
    Attempt to open an application by name.
    Returns a status dict.
    """
    name = name.strip()
    try:
        if _is_windows():
            # start is a shell builtin
            subprocess.Popen(["cmd", "/c", "start", "", name], shell=False)
        elif _is_macos():
            subprocess.Popen(["open", "-a", name])
        else:
            # Linux – try common launchers
            if shutil.which(name):
                subprocess.Popen([name])
            else:
                # fallback: xdg-open or just try the name
                subprocess.Popen(["xdg-open", name])
        return {"ok": True, "action": "open_app", "target": name, "message": f"Launched '{name}'"}
    except Exception as e:
        return {"ok": False, "action": "open_app", "target": name, "error": str(e)}


def open_folder(path: str | Path) -> Dict[str, Any]:
    """Open a folder in the system file manager."""
    path = Path(path).expanduser().resolve()
    if not path.exists():
        return {"ok": False, "action": "open_folder", "target": str(path), "error": "Path does not exist"}
    if not path.is_dir():
        return {"ok": False, "action": "open_folder", "target": str(path), "error": "Not a directory"}

    try:
        if _is_windows():
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif _is_macos():
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return {"ok": True, "action": "open_folder", "target": str(path), "message": f"Opened folder: {path}"}
    except Exception as e:
        return {"ok": False, "action": "open_folder", "target": str(path), "error": str(e)}


def search_files(
    root: str | Path,
    pattern: str = "*",
    max_results: int = 50,
) -> Dict[str, Any]:
    """
    Simple recursive file search under `root`.
    `pattern` supports basic glob (e.g. "*.pdf", "*invoice*").
    """
    root = Path(root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return {"ok": False, "error": f"Root path invalid: {root}", "results": []}

    results: List[str] = []
    try:
        for p in root.rglob(pattern):
            if p.is_file():
                results.append(str(p))
                if len(results) >= max_results:
                    break
    except Exception as e:
        return {"ok": False, "error": str(e), "results": results}

    return {
        "ok": True,
        "root": str(root),
        "pattern": pattern,
        "count": len(results),
        "results": results,
    }


# Registry of tools that agents can declare
TOOL_REGISTRY = {
    "read_document": read_document,
    "summarize_text": summarize_text,
    "open_application": open_application,
    "open_folder": open_folder,
    "search_files": search_files,
}
