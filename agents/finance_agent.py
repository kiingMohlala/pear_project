"""
Finance Agent (v0.40) – statement import, categorization, analysis, recommendations.

Uses KnowledgeStore for historical Q&A, jobs for large imports, and tracing spans.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import Agent
from core.task import Task
from core.llm import BaseLLM, create_llm
from core.finance import (
    Transaction,
    parse_csv_transactions,
    parse_statement_file,
    monthly_summary,
    spending_by_category,
    detect_recurring,
    detect_anomalies,
    recommendations,
    transactions_to_searchable_text,
    categorize,
)


FINANCE_SYSTEM = """You are PEAR's Finance Agent. You analyze personal bank transactions.
Be precise with numbers. Prefer structured bullet summaries. This is not professional financial advice.
"""


class FinanceAgent(Agent):
    def __init__(self, llm: Optional[BaseLLM] = None, **kwargs):
        super().__init__(
            name="finance",
            description=(
                "Analyses bank statements, budgets, and expenses. "
                "Imports CSV/PDF statements, categorizes transactions, "
                "computes cash flow, detects recurring payments and anomalies, "
                "and answers questions about spending history."
            ),
            capabilities=[
                "finance",
                "analysis",
                "budget",
                "invoice",
                "cashflow",
                "transactions",
            ],
            allowed_tools=["read_document", "summarize_text"],
            system_prompt=FINANCE_SYSTEM,
            **kwargs,
        )
        self.llm: BaseLLM = llm or create_llm()

    def can_handle(self, task: Task) -> float:
        obj = task.objective.lower()
        score = super().can_handle(task)
        signals = [
            "budget", "invoice", "bank statement", "expense", "finance",
            "cashflow", "cash flow", "transaction", "spending", "salary",
            "subscription", "import statement", "csv",
        ]
        hits = sum(1 for s in signals if s in obj)
        if hits:
            score = max(score, min(0.95, 0.55 + 0.1 * hits))
        return score

    # ── ledger stored on knowledge metadata ───────────────────────

    def _ledger(self) -> List[Transaction]:
        meta = self.memory.knowledge.__dict__.setdefault("_finance_ledger", [])
        # also recover from documents tagged finance_ledger
        if not meta:
            for doc in self.memory.knowledge.list_documents():
                if (doc.get("metadata") or {}).get("type") == "finance_ledger":
                    for row in (doc.get("metadata") or {}).get("transactions") or []:
                        meta.append(Transaction.from_dict(row))
        return meta

    def _save_ledger(self, txns: List[Transaction]) -> None:
        self.memory.knowledge.__dict__["_finance_ledger"] = txns
        text = transactions_to_searchable_text(txns)
        # upsert ledger doc for semantic retrieval
        existing = [
            d for d in self.memory.knowledge.documents
            if (d.get("metadata") or {}).get("type") == "finance_ledger"
        ]
        payload = {
            "type": "finance_ledger",
            "transactions": [t.to_dict() for t in txns],
            "count": len(txns),
        }
        if existing:
            doc = existing[-1]
            # re-index: remove old vectors and replace
            self.memory.knowledge.vectors.delete_by_metadata(doc_id=doc["id"])
            doc["text"] = text
            doc["metadata"] = payload
            self.memory.knowledge._index_document(doc)
        else:
            self.memory.knowledge.add_document(
                name="finance_ledger",
                text=text,
                metadata=payload,
            )
        try:
            self.memory._save()
        except Exception:
            pass

    def _process(self, task: Task, **kwargs) -> Dict[str, Any]:
        objective = task.objective
        lower = objective.lower()

        # Import path: /finance import <path> or "import statement <path>"
        path = self._extract_path(objective)
        if path or any(k in lower for k in ("import statement", "import csv", "load statement", "upload statement")):
            return self._import_statement(path, objective)

        if any(k in lower for k in ("monthly summary", "cash flow", "cashflow", "summary")):
            return self._report_summary()

        if any(k in lower for k in ("by category", "spending by", "categories", "breakdown")):
            return self._report_categories()

        if any(k in lower for k in ("recurring", "subscription")):
            return self._report_recurring()

        if any(k in lower for k in ("anomal", "unusual", "duplicate", "large expense")):
            return self._report_anomalies()

        if any(k in lower for k in ("recommend", "advice", "save money", "budget tip")):
            return self._report_recommendations()

        if any(k in lower for k in ("full report", "finance report", "analyze my finances", "analyse my finances")):
            return self._full_report()

        # Historical question via retrieval + optional ledger filter
        return self._answer_question(objective)

    # ── import ────────────────────────────────────────────────────

    def _extract_path(self, text: str) -> Optional[Path]:
        # quoted path
        m = re.search(r'["\']([^"\']+\.(?:csv|tsv|txt|pdf|xlsx))["\']', text, re.I)
        if m:
            return Path(m.group(1)).expanduser()
        m = re.search(r"(/[^\s]+\.(?:csv|tsv|txt|pdf|xlsx))", text, re.I)
        if m:
            return Path(m.group(1)).expanduser()
        m = re.search(r"(~/[^\s]+\.(?:csv|tsv|txt|pdf|xlsx))", text, re.I)
        if m:
            return Path(m.group(1)).expanduser()
        return None

    def _import_statement(self, path: Optional[Path], objective: str) -> Dict[str, Any]:
        try:
            from core.tracing import get_tracer
            tracer = get_tracer()
        except Exception:
            tracer = None

        if path is None:
            return {
                "ok": False,
                "reply": (
                    "Provide a statement path, e.g.\n"
                    "  import statement /path/to/bank.csv"
                ),
                "action": "need_path",
            }

        # Large file → background job if planner available
        try:
            size = path.stat().st_size
        except OSError as e:
            return {"ok": False, "reply": f"Cannot read file: {e}", "action": "import_error"}

        if size > 500_000 and self.planner and not (objective.lower().find("foreground") >= 0):
            result = self.planner.submit_job(
                f"import statement {path} foreground",
                priority="normal",
            )
            return {
                "ok": True,
                "reply": (
                    f"Statement is large ({size} bytes). "
                    f"Queued background job {result.get('job_id')} for import."
                ),
                "action": "import_queued",
                "job_id": result.get("job_id"),
            }

        span = None
        if tracer:
            span = tracer.start_span("finance.parse", kind="agent", path=str(path))
        try:
            new_txns = parse_statement_file(path)
        except Exception as e:
            if span:
                tracer.end_span(span, status="error", error=str(e))
            return {"ok": False, "reply": f"Import failed: {e}", "action": "import_error"}
        if span:
            tracer.end_span(span, status="ok", count=len(new_txns))

        if tracer:
            span = tracer.start_span("finance.categorize", kind="agent", count=len(new_txns))
        for t in new_txns:
            if t.category == "uncategorized":
                t.category = categorize(t.description, t.amount)
        if span and tracer:
            tracer.end_span(span, status="ok")

        ledger = self._ledger()
        # de-dupe by date+description+amount
        existing_keys = {
            (t.date, t.description.lower(), round(t.amount, 2)) for t in ledger
        }
        added = 0
        for t in new_txns:
            key = (t.date, t.description.lower(), round(t.amount, 2))
            if key not in existing_keys:
                ledger.append(t)
                existing_keys.add(key)
                added += 1

        if tracer:
            span = tracer.start_span("finance.index", kind="retrieval", added=added)
        self._save_ledger(ledger)
        if span and tracer:
            tracer.end_span(span, status="ok")

        months = monthly_summary(ledger)
        return {
            "ok": True,
            "reply": (
                f"Imported **{added}** new transactions from `{path.name}` "
                f"(ledger total: {len(ledger)}).\n"
                f"Months covered: {', '.join(months.keys()) or 'n/a'}.\n"
                "Ask for a monthly summary, spending by category, recurring payments, or anomalies."
            ),
            "action": "import_complete",
            "added": added,
            "total": len(ledger),
        }

    # ── reports ───────────────────────────────────────────────────

    def _require_ledger(self) -> Optional[Dict[str, Any]]:
        if not self._ledger():
            return {
                "ok": True,
                "reply": (
                    "No transactions loaded yet. Import a CSV statement first:\n"
                    "  import statement /path/to/export.csv"
                ),
                "action": "need_data",
            }
        return None

    def _report_summary(self) -> Dict[str, Any]:
        err = self._require_ledger()
        if err:
            return err
        try:
            from core.tracing import get_tracer
            with get_tracer().span("finance.summary", kind="agent"):
                return self._summary_body()
        except Exception:
            return self._summary_body()

    def _summary_body(self) -> Dict[str, Any]:
        txns = self._ledger()
        months = monthly_summary(txns)
        lines = ["## Monthly cash flow\n"]
        for m, v in months.items():
            lines.append(
                f"- **{m}**: income {v['income']:.2f} · expenses {v['expenses']:.2f} · net {v['net']:+.2f}"
            )
        income = sum(t.amount for t in txns if t.amount > 0)
        expenses = sum(-t.amount for t in txns if t.amount < 0)
        lines.append(f"\n**Totals** — income {income:.2f}, expenses {expenses:.2f}, net {income - expenses:+.2f}")
        lines.append(f"Transactions: {len(txns)}")
        return {"ok": True, "reply": "\n".join(lines), "action": "monthly_summary", "months": months}

    def _report_categories(self) -> Dict[str, Any]:
        err = self._require_ledger()
        if err:
            return err
        cats = spending_by_category(self._ledger())
        lines = ["## Spending by category\n"]
        for c, amt in cats.items():
            lines.append(f"- **{c}**: {amt:.2f}")
        return {"ok": True, "reply": "\n".join(lines), "action": "by_category", "categories": cats}

    def _report_recurring(self) -> Dict[str, Any]:
        err = self._require_ledger()
        if err:
            return err
        rec = detect_recurring(self._ledger())
        if not rec:
            return {"ok": True, "reply": "No recurring payments detected yet.", "action": "recurring"}
        lines = ["## Recurring payments\n"]
        for r in rec[:15]:
            lines.append(
                f"- {r['description'][:50]} — ~{r['avg_amount']:.2f} × {r['count']} ({r['category']})"
            )
        return {"ok": True, "reply": "\n".join(lines), "action": "recurring", "recurring": rec}

    def _report_anomalies(self) -> Dict[str, Any]:
        err = self._require_ledger()
        if err:
            return err
        findings = detect_anomalies(self._ledger())
        if not findings:
            return {"ok": True, "reply": "No anomalies flagged.", "action": "anomalies"}
        lines = ["## Flags\n"]
        for f in findings[:20]:
            lines.append(
                f"- **[{f['type']}]** {f['date']} {f['description'][:40]} "
                f"{f['amount']} — {f['detail']}"
            )
        return {"ok": True, "reply": "\n".join(lines), "action": "anomalies", "findings": findings}

    def _report_recommendations(self) -> Dict[str, Any]:
        err = self._require_ledger()
        if err:
            return err
        tips = recommendations(self._ledger())
        lines = ["## Recommendations\n"] + [f"- {t}" for t in tips]
        return {"ok": True, "reply": "\n".join(lines), "action": "recommendations", "tips": tips}

    def _full_report(self) -> Dict[str, Any]:
        err = self._require_ledger()
        if err:
            return err
        try:
            from core.tracing import get_tracer
            with get_tracer().span("finance.full_report", kind="agent"):
                parts = [
                    self._summary_body()["reply"],
                    self._report_categories()["reply"],
                    self._report_recurring()["reply"],
                    self._report_anomalies()["reply"],
                    self._report_recommendations()["reply"],
                ]
        except Exception:
            parts = [
                self._summary_body()["reply"],
                self._report_categories()["reply"],
                self._report_recurring()["reply"],
                self._report_anomalies()["reply"],
                self._report_recommendations()["reply"],
            ]
        return {
            "ok": True,
            "reply": "\n\n".join(parts),
            "action": "full_report",
        }

    def _answer_question(self, question: str) -> Dict[str, Any]:
        err = self._require_ledger()
        if err:
            # still try knowledge retrieval for finance docs
            ctx = self.memory.knowledge.build_context(question, max_chars=4000)
            if not ctx:
                return err

        txns = self._ledger()
        # Lightweight filter: keyword match on descriptions
        tokens = [t for t in re.split(r"\W+", question.lower()) if len(t) > 3]
        matched = [
            t for t in txns
            if any(tok in t.description.lower() or tok in t.category for tok in tokens)
        ][:15]

        ctx = self.memory.knowledge.build_context(question, max_chars=4000)
        lines = ["## Answer\n"]
        if matched:
            lines.append("Matching transactions:")
            for t in matched:
                lines.append(f"- {t.date} {t.amount:+.2f} [{t.category}] {t.description}")
        elif txns:
            # fall back to category totals if asking about a category
            cats = spending_by_category(txns)
            for tok in tokens:
                if tok in cats:
                    lines.append(f"Spending on **{tok}**: {cats[tok]:.2f}")
                    break
            else:
                lines.append(self._summary_body()["reply"])
        if ctx:
            lines.append("\n_Retrieved ledger context used for grounding._")
        return {
            "ok": True,
            "reply": "\n".join(lines),
            "action": "finance_qa",
            "matches": len(matched),
        }
