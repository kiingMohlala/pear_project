"""
Legal Agent (v0.50) – production contract analysis.

Import → type detect → clause extract → risks / summary / Q&A / compare.
Uses KnowledgeStore, jobs, tracing. Not legal advice.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import Agent
from core.task import Task
from core.llm import BaseLLM, create_llm
from core.legal import (
    Clause,
    detect_document_type,
    extract_clauses,
    tag_concepts,
    find_missing_clauses,
    compare_clause_lists,
    clauses_to_searchable_text,
)

# Heuristic risk patterns (offline fallback)
RISK_PATTERNS: List[Tuple[str, str, str]] = [
    (r"\bindemnif(?:y|ication)\b", "high", "Indemnification obligation"),
    (r"\bunlimited\s+liability\b", "critical", "Unlimited liability"),
    (r"\bnon[- ]?compete\b", "high", "Non-compete restriction"),
    (r"\bnon[- ]?solicit", "medium", "Non-solicitation"),
    (r"\bperpetual\b", "high", "Perpetual obligation / term"),
    (r"\birrevocable\b", "high", "Irrevocable grant or waiver"),
    (r"\bautomatic(?:ally)?\s+renew", "medium", "Automatic renewal"),
    (r"\bwaiver\s+of\s+(?:jury|class)\b", "medium", "Waiver of jury/class action"),
    (r"\bgoverning\s+law\b", "low", "Governing law clause"),
    (r"\barbitration\b", "medium", "Mandatory arbitration"),
    (r"\bconfidential(?:ity)?\b", "low", "Confidentiality"),
    (r"\btermination\s+(?:for\s+)?convenience\b", "medium", "Termination for convenience"),
    (r"\bintellectual\s+property\b|\bwork\s+for\s+hire\b", "high", "IP ownership / work-for-hire"),
    (r"\bexclusive\b", "medium", "Exclusivity"),
    (r"\bliquidated\s+damages\b", "high", "Liquidated damages"),
    (r"\bhold\s+harmless\b", "high", "Hold-harmless"),
    (r"\binjunctive\s+relief\b", "medium", "Injunctive relief"),
    (r"\bdata\s+protection\b|\bGDPR\b|\bPOPIA\b", "medium", "Data protection"),
]


LEGAL_SYSTEM = """You are PEAR's Legal Agent. You review contracts and NDAs for non-lawyers.
Be precise, cite language, flag severity (critical/high/medium/low).
Never invent clauses. This is informational review, not legal advice.
Prefer structured headings and bullets.
"""


class LegalAgent(Agent):
    def __init__(self, llm: Optional[BaseLLM] = None, **kwargs):
        super().__init__(
            name="legal",
            description=(
                "Reviews contracts, NDAs, leases, and terms of service: detects document type, "
                "extracts structured clauses, flags risks, compares versions, and answers "
                "questions over uploaded legal documents."
            ),
            capabilities=[
                "legal",
                "document_review",
                "contract",
                "risk_analysis",
                "clause_extraction",
                "nda",
            ],
            allowed_tools=["read_document", "summarize_text"],
            system_prompt=LEGAL_SYSTEM,
            **kwargs,
        )
        self.llm: BaseLLM = llm or create_llm()

    def can_handle(self, task: Task) -> float:
        obj = task.objective.lower()
        score = super().can_handle(task)
        signals = [
            "contract", "nda", "legal", "terms of service", "clause",
            "liability", "risk", "indemnif", "agreement", "review this",
            "non-disclosure", "tos", "msa", "sla", "lease", "compare contract",
        ]
        hits = sum(1 for s in signals if s in obj)
        if hits:
            score = max(score, min(0.95, 0.55 + 0.1 * hits))
        return score

    def _process(self, task: Task, **kwargs) -> Dict[str, Any]:
        objective = task.objective
        lower = objective.lower()

        # Import / load file
        path = self._extract_path(objective)
        if path or any(k in lower for k in ("import contract", "import legal", "load contract")):
            return self._import_document(path, objective)

        # Compare two docs
        if "compare" in lower:
            return self._compare(objective)

        doc_text, doc_name = self._resolve_document(objective)
        if not doc_text:
            return {
                "ok": True,
                "reply": (
                    "No legal document loaded. Import one:\n"
                    "  import contract /path/to/nda.pdf\n"
                    "or upload with `/file <path>` then ask me to review it."
                ),
                "action": "need_document",
            }

        if any(k in lower for k in ("extract", "clause", "section", "provision", "list clauses")):
            return self._extract_clauses(doc_text, doc_name, objective)

        if any(k in lower for k in ("risk", "liability", "red flag", "concern", "danger")):
            return self._risk_analysis(doc_text, doc_name, objective)

        if any(k in lower for k in ("summar", "executive", "overview", "tl;dr", "brief")):
            return self._executive_summary(doc_text, doc_name, objective)

        if any(k in lower for k in ("document type", "what kind", "what type of")):
            return self._type_report(doc_text, doc_name)

        if any(k in lower for k in ("missing clause", "what's missing", "gaps")):
            return self._missing_report(doc_text, doc_name)

        # Q&A-ish questions
        if any(k in lower for k in ("where", "what does", "who", "when", "how long", "which clause", "?")):
            return self._answer_question(doc_text, doc_name, objective)

        return self._full_review(doc_text, doc_name, objective)

    # ── tracing helper ────────────────────────────────────────────

    def _span(self, name: str, **attrs):
        try:
            from core.tracing import get_tracer
            return get_tracer().span(name, kind="agent", **attrs)
        except Exception:
            from contextlib import nullcontext
            return nullcontext()

    # ── import ────────────────────────────────────────────────────

    def _extract_path(self, text: str) -> Optional[Path]:
        m = re.search(r'["\']([^"\']+\.(?:pdf|docx|txt|md))["\']', text, re.I)
        if m:
            return Path(m.group(1)).expanduser()
        m = re.search(r"(/[^\s]+\.(?:pdf|docx|txt|md))", text, re.I)
        if m:
            return Path(m.group(1)).expanduser()
        m = re.search(r"(~/[^\s]+\.(?:pdf|docx|txt|md))", text, re.I)
        if m:
            return Path(m.group(1)).expanduser()
        return None

    def _import_document(self, path: Optional[Path], objective: str) -> Dict[str, Any]:
        if path is None:
            return {
                "ok": False,
                "reply": "Provide a path, e.g. import contract /path/to/nda.txt",
                "action": "need_path",
            }
        if not path.exists():
            return {"ok": False, "reply": f"File not found: {path}", "action": "import_error"}

        try:
            size = path.stat().st_size
        except OSError as e:
            return {"ok": False, "reply": str(e), "action": "import_error"}

        if size > 400_000 and self.planner and "foreground" not in objective.lower():
            result = self.planner.submit_job(
                f"import contract {path} foreground",
                priority="normal",
            )
            return {
                "ok": True,
                "reply": f"Large contract ({size} bytes). Queued job {result.get('job_id')}.",
                "action": "import_queued",
                "job_id": result.get("job_id"),
            }

        with self._span("legal.parse", path=str(path)):
            try:
                from core.tools import read_document
                text = read_document(path) if path.suffix.lower() in (".pdf", ".docx") else path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except Exception as e:
                return {"ok": False, "reply": f"Could not read document: {e}", "action": "import_error"}

        if not (text or "").strip():
            return {"ok": False, "reply": "Document appears empty.", "action": "import_error"}

        with self._span("legal.structure", chars=len(text)):
            doc_type = detect_document_type(text, path.name)
            clauses = extract_clauses(text)
            indexed = clauses_to_searchable_text(clauses, path.name, doc_type)

        doc = self.memory.knowledge.add_document(
            name=path.name,
            text=text,
            source_path=str(path),
            metadata={
                "type": "legal_document",
                "doc_type": doc_type,
                "clause_count": len(clauses),
                "clauses": [c.to_dict() for c in clauses],
                "concepts": sorted({c for cl in clauses for c in cl.concepts}),
            },
        )
        # Also index structured clause view for retrieval
        self.memory.knowledge.add_document(
            name=f"{path.name}::clauses",
            text=indexed,
            metadata={"type": "legal_clauses", "parent": doc.get("id"), "doc_type": doc_type},
        )
        try:
            self.memory._save()
        except Exception:
            pass

        return {
            "ok": True,
            "reply": (
                f"Imported **{path.name}** as `{doc_type}` with **{len(clauses)}** clauses.\n"
                "Ask for risks, executive summary, clause list, missing clauses, or Q&A."
            ),
            "action": "import_complete",
            "document": path.name,
            "doc_type": doc_type,
            "clause_count": len(clauses),
        }

    # ── document resolution ───────────────────────────────────────

    def _resolve_document(self, objective: str) -> Tuple[str, str]:
        docs = [
            d for d in self.memory.knowledge.list_documents()
            if (d.get("metadata") or {}).get("type") != "legal_clauses"
        ]
        if not docs:
            docs = self.memory.knowledge.list_documents()
        if not docs:
            return "", ""

        lower = objective.lower()
        for doc in reversed(docs):
            name = (doc.get("name") or "").lower()
            if name and any(tok in lower for tok in re.split(r"\W+", name) if len(tok) > 3):
                return doc.get("text") or "", doc.get("name") or "document"

        hits = self.memory.knowledge.search(objective, limit=3, include_notes=False)
        for h in hits:
            if h.get("type") == "document":
                for doc in docs:
                    if doc.get("id") == h.get("id"):
                        return doc.get("text") or "", doc.get("name") or "document"

        latest = docs[-1]
        return latest.get("text") or "", latest.get("name") or "document"

    def _clauses_for(self, text: str, name: str) -> Tuple[str, List[Clause]]:
        # Prefer cached structured clauses
        for doc in self.memory.knowledge.list_documents():
            meta = doc.get("metadata") or {}
            if meta.get("type") == "legal_document" and doc.get("name") == name and meta.get("clauses"):
                clauses = [Clause.from_dict(c) for c in meta["clauses"]]
                return meta.get("doc_type") or detect_document_type(text, name), clauses
        doc_type = detect_document_type(text, name)
        with self._span("legal.clause_extraction", document=name):
            clauses = extract_clauses(text)
        return doc_type, clauses

    # ── analyses ──────────────────────────────────────────────────

    def _type_report(self, text: str, name: str) -> Dict[str, Any]:
        doc_type = detect_document_type(text, name)
        concepts = tag_concepts(text)
        return {
            "ok": True,
            "reply": (
                f"**{name}** detected as `{doc_type}`.\n"
                f"Concepts: {', '.join(concepts) or 'n/a'}"
            ),
            "action": "doc_type",
            "doc_type": doc_type,
            "concepts": concepts,
        }

    def _extract_clauses(self, text: str, name: str, objective: str) -> Dict[str, Any]:
        doc_type, clauses = self._clauses_for(text, name)
        lines = [f"## Clauses in **{name}** (`{doc_type}`)\n"]
        for c in clauses[:30]:
            concepts = f" · {', '.join(c.concepts)}" if c.concepts else ""
            preview = c.text.replace("\n", " ")[:180]
            lines.append(f"- **{c.number} {c.title}**{concepts}\n  {preview}{'…' if len(c.text) > 180 else ''}")
        return {
            "ok": True,
            "reply": "\n".join(lines),
            "action": "clause_extraction",
            "document": name,
            "doc_type": doc_type,
            "clauses": [c.to_dict() for c in clauses],
        }

    def _heuristic_risks(self, text: str) -> List[Dict[str, Any]]:
        risks = []
        for pat, severity, label in RISK_PATTERNS:
            for m in re.finditer(pat, text, re.I):
                start = max(0, m.start() - 40)
                end = min(len(text), m.end() + 80)
                snippet = re.sub(r"\s+", " ", text[start:end]).strip()
                risks.append({"severity": severity, "label": label, "snippet": snippet})
        # de-dupe by label
        seen = set()
        unique = []
        for r in risks:
            if r["label"] not in seen:
                seen.add(r["label"])
                unique.append(r)
        return unique

    def _risk_analysis(self, text: str, name: str, objective: str) -> Dict[str, Any]:
        with self._span("legal.risk_analysis", document=name):
            risks = self._heuristic_risks(text)
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        risks.sort(key=lambda r: order.get(r["severity"], 9))
        if not risks:
            return {
                "ok": True,
                "reply": f"No high-signal risk patterns found in **{name}**.",
                "action": "risk_analysis",
                "document": name,
                "risks": [],
            }
        lines = [f"## Risk analysis — **{name}**\n"]
        for r in risks:
            lines.append(f"- **[{r['severity'].upper()}]** {r['label']}")
            if r.get("snippet"):
                lines.append(f"  “{r['snippet']}”")
        lines.append("\n_Automated scan — not legal advice._")
        return {
            "ok": True,
            "reply": "\n".join(lines),
            "action": "risk_analysis",
            "document": name,
            "risks": risks,
        }

    def _executive_summary(self, text: str, name: str, objective: str) -> Dict[str, Any]:
        with self._span("legal.summary", document=name):
            doc_type, clauses = self._clauses_for(text, name)
            risks = self._heuristic_risks(text)
            missing = find_missing_clauses(doc_type, clauses)
            deadlines = []
            for c in clauses:
                deadlines.extend(c.dates)
            high = [r for r in risks if r["severity"] in ("critical", "high")]

        lines = [
            f"## Executive summary — **{name}**",
            f"Type: `{doc_type}` · Clauses: {len(clauses)}",
            "",
            "### Key clauses",
        ]
        for c in clauses[:8]:
            lines.append(f"- {c.number} {c.title}")
        if high:
            lines.append("\n### Top risks")
            for r in high[:6]:
                lines.append(f"- **[{r['severity'].upper()}]** {r['label']}")
        if deadlines:
            lines.append("\n### Dates / periods mentioned")
            lines.append("- " + "; ".join(sorted(set(deadlines))[:10]))
        if missing:
            lines.append("\n### Possibly missing")
            for m in missing:
                lines.append(f"- {m}")
        lines.append("\n_Informational review only — not legal advice._")
        return {
            "ok": True,
            "reply": "\n".join(lines),
            "action": "executive_summary",
            "document": name,
            "doc_type": doc_type,
            "risks": risks,
            "missing": missing,
        }

    def _missing_report(self, text: str, name: str) -> Dict[str, Any]:
        doc_type, clauses = self._clauses_for(text, name)
        missing = find_missing_clauses(doc_type, clauses)
        if not missing:
            return {
                "ok": True,
                "reply": f"No obvious gaps for a typical `{doc_type}` checklist.",
                "action": "missing_clauses",
            }
        lines = [f"## Possibly missing for `{doc_type}`\n"] + [f"- {m}" for m in missing]
        return {"ok": True, "reply": "\n".join(lines), "action": "missing_clauses", "missing": missing}

    def _full_review(self, text: str, name: str, objective: str) -> Dict[str, Any]:
        with self._span("legal.full_review", document=name):
            summary = self._executive_summary(text, name, objective)
            risks = self._risk_analysis(text, name, objective)
        reply = summary["reply"] + "\n\n" + risks["reply"]
        return {
            "ok": True,
            "reply": reply,
            "action": "full_review",
            "document": name,
            "risks": risks.get("risks"),
            "doc_type": summary.get("doc_type"),
        }

    def _answer_question(self, text: str, name: str, objective: str) -> Dict[str, Any]:
        with self._span("legal.qa", document=name):
            doc_type, clauses = self._clauses_for(text, name)
            ctx = self.memory.knowledge.build_context(objective, max_chars=4000)
            tokens = [t for t in re.split(r"\W+", objective.lower()) if len(t) > 3]
            matched = []
            for c in clauses:
                blob = f"{c.title} {c.text}".lower()
                if any(t in blob for t in tokens):
                    matched.append(c)

        lines = [f"## Q&A — **{name}** (`{doc_type}`)\n"]
        if matched:
            lines.append("Relevant clauses:")
            for c in matched[:6]:
                preview = c.text.replace("\n", " ")[:220]
                lines.append(f"- **{c.number} {c.title}**: {preview}…")
        else:
            lines.append("No direct clause match; showing retrieval context.")
            if ctx:
                lines.append(ctx[:1500])
        lines.append("\n_Not legal advice._")
        return {
            "ok": True,
            "reply": "\n".join(lines),
            "action": "legal_qa",
            "document": name,
            "matches": len(matched),
        }

    def _compare(self, objective: str) -> Dict[str, Any]:
        docs = [
            d for d in self.memory.knowledge.list_documents()
            if (d.get("metadata") or {}).get("type") == "legal_document"
            or (
                (d.get("metadata") or {}).get("type") not in ("legal_clauses", "finance_ledger")
                and d.get("text")
            )
        ]
        # Prefer explicit names in objective
        named = []
        lower = objective.lower()
        for d in docs:
            n = (d.get("name") or "").lower()
            if n and n.split(".")[0] in lower:
                named.append(d)
        if len(named) >= 2:
            a, b = named[0], named[1]
        elif len(docs) >= 2:
            a, b = docs[-2], docs[-1]
        else:
            return {
                "ok": False,
                "reply": "Need two contracts loaded to compare. Import both first.",
                "action": "need_two_docs",
            }

        with self._span("legal.compare", a=a.get("name"), b=b.get("name")):
            _, ca = self._clauses_for(a.get("text") or "", a.get("name") or "A")
            _, cb = self._clauses_for(b.get("text") or "", b.get("name") or "B")
            diff = compare_clause_lists(ca, cb)

        lines = [
            f"## Compare **{a.get('name')}** → **{b.get('name')}**\n",
            f"Added: {len(diff['added'])} · Removed: {len(diff['removed'])} · Modified: {len(diff['modified'])}\n",
        ]
        for item in diff["added"][:8]:
            c = item["clause"]
            lines.append(f"+ **{c.get('number')} {c.get('title')}**")
        for item in diff["removed"][:8]:
            c = item["clause"]
            lines.append(f"- **{c.get('number')} {c.get('title')}**")
        for item in diff["modified"][:8]:
            before, after = item["before"], item["after"]
            lines.append(
                f"~ **{before.get('title')}** → **{after.get('title')}** "
                f"(similarity {item['similarity']})"
            )
        return {
            "ok": True,
            "reply": "\n".join(lines),
            "action": "compare",
            "diff": {
                "added": len(diff["added"]),
                "removed": len(diff["removed"]),
                "modified": len(diff["modified"]),
            },
        }
