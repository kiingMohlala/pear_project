"""
Finance domain helpers: transaction schema, statement parsing, categorization.
Used by FinanceAgent — no agent logic here.
"""

from __future__ import annotations

import csv
import io
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ── schema ────────────────────────────────────────────────────────

@dataclass
class Transaction:
    id: str
    date: str  # ISO YYYY-MM-DD
    description: str
    amount: float  # signed: +income, -expense
    category: str = "uncategorized"
    account: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Transaction":
        return cls(
            id=str(d.get("id") or f"txn_{uuid.uuid4().hex[:10]}"),
            date=str(d.get("date") or ""),
            description=str(d.get("description") or ""),
            amount=float(d.get("amount") or 0),
            category=str(d.get("category") or "uncategorized"),
            account=str(d.get("account") or ""),
            raw=dict(d.get("raw") or {}),
        )


# ── category rules (keyword → category) ───────────────────────────

CATEGORY_RULES: List[Tuple[str, List[str]]] = [
    ("salary", ["payroll", "salary", "wage", "direct dep", "employer"]),
    ("transfer", ["transfer", "xfer", "venmo", "zelle", "paypal", "cash app"]),
    ("rent", ["rent", "landlord", "lease payment"]),
    ("mortgage", ["mortgage", "home loan"]),
    ("utilities", ["electric", "water bill", "gas bill", "utility", "internet", "wifi", "comcast", "verizon"]),
    ("groceries", ["grocery", "supermarket", "whole foods", "trader joe", "walmart", "costco", "aldi", "kroger"]),
    ("dining", ["restaurant", "cafe", "coffee", "starbucks", "mcdonald", "uber eats", "doordash", "grubhub"]),
    ("transport", ["uber", "lyft", "fuel", "shell", "chevron", "parking", "transit", "metro"]),
    ("subscription", ["netflix", "spotify", "disney+", "hulu", "apple.com/bill", "google *", "microsoft", "adobe", "subscription"]),
    ("shopping", ["amazon", "target", "ebay", "etsy", "best buy"]),
    ("healthcare", ["pharmacy", "cvs", "walgreens", "hospital", "clinic", "dental", "medical"]),
    ("insurance", ["insurance", "geico", "allstate", "premium"]),
    ("entertainment", ["cinema", "movie", "steam", "ticketmaster", "concert"]),
    ("fees", ["fee", "overdraft", "service charge", "atm fee", "interest charge"]),
    ("income", ["refund", "rebate", "dividend", "interest paid", "tax refund"]),
]


def categorize(description: str, amount: float = 0.0) -> str:
    d = description.lower()
    for cat, keywords in CATEGORY_RULES:
        if any(k in d for k in keywords):
            if cat == "salary" and amount < 0:
                continue
            return cat
    if amount > 0:
        return "income"
    return "uncategorized"


# ── parsing ───────────────────────────────────────────────────────

DATE_FORMATS = [
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%m-%d-%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
    "%m/%d/%y",
    "%d %b %Y",
    "%b %d, %Y",
]


def parse_date(value: str) -> Optional[str]:
    value = (value or "").strip().strip('"')
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # try first 10 chars ISO
    if re.match(r"\d{4}-\d{2}-\d{2}", value):
        return value[:10]
    return None


def parse_amount(value: str) -> Optional[float]:
    if value is None:
        return None
    s = str(value).strip().strip('"')
    if not s:
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = s.replace(",", "").replace("$", "").replace("€", "").replace("£", "").replace("R", "")
    if s.endswith("-"):
        neg = True
        s = s[:-1]
    if s.startswith("-"):
        neg = True
        s = s[1:]
    try:
        amt = float(s)
    except ValueError:
        return None
    return -abs(amt) if neg else amt


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())


HEADER_MAP = {
    "date": {"date", "transactiondate", "posteddate", "postingdate", "transdate"},
    "description": {"description", "memo", "narration", "details", "payee", "name", "transaction"},
    "amount": {"amount", "value", "transactionamount", "sum"},
    "debit": {"debit", "withdrawal", "outflow", "moneyout"},
    "credit": {"credit", "deposit", "inflow", "moneyin"},
    "account": {"account", "accountname", "accountnumber"},
}


def _map_headers(fieldnames: List[str]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    norms = {_norm_header(f): f for f in fieldnames}
    for target, aliases in HEADER_MAP.items():
        for n, original in norms.items():
            if n in aliases:
                mapping[target] = original
                break
    return mapping


def parse_csv_transactions(text: str, account: str = "") -> List[Transaction]:
    """Parse CSV text into normalized transactions."""
    text = text.lstrip("\ufeff")
    # sniff dialect
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")
    mapping = _map_headers(list(reader.fieldnames))
    if "date" not in mapping or "description" not in mapping:
        raise ValueError(
            f"Unrecognized CSV headers: {reader.fieldnames}. "
            "Need at least Date and Description columns."
        )

    txns: List[Transaction] = []
    for i, row in enumerate(reader):
        if not any((v or "").strip() for v in row.values()):
            continue
        date = parse_date(row.get(mapping["date"], ""))
        desc = (row.get(mapping["description"]) or "").strip()
        if not date or not desc:
            continue

        amount: Optional[float] = None
        if "amount" in mapping:
            amount = parse_amount(row.get(mapping["amount"], ""))
        else:
            debit = parse_amount(row.get(mapping.get("debit", ""), "")) if "debit" in mapping else None
            credit = parse_amount(row.get(mapping.get("credit", ""), "")) if "credit" in mapping else None
            if credit is not None and credit != 0:
                amount = abs(credit)
            elif debit is not None and debit != 0:
                amount = -abs(debit)
        if amount is None:
            continue

        acct = account
        if "account" in mapping:
            acct = (row.get(mapping["account"]) or account or "").strip()

        cat = categorize(desc, amount)
        txns.append(
            Transaction(
                id=f"txn_{uuid.uuid4().hex[:10]}",
                date=date,
                description=desc,
                amount=amount,
                category=cat,
                account=acct,
                raw=dict(row),
            )
        )
    return txns


def parse_statement_file(path: Path, account: str = "") -> List[Transaction]:
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(str(path))
    suffix = path.suffix.lower()
    if suffix in (".csv", ".txt", ".tsv"):
        text = path.read_text(encoding="utf-8", errors="replace")
        return parse_csv_transactions(text, account=account or path.stem)
    if suffix in (".xlsx", ".xls"):
        raise ValueError(
            "Excel import requires openpyxl (optional). "
            "Export to CSV, or install openpyxl and retry."
        )
    if suffix == ".pdf":
        # reuse document reader → text, then fail clearly if not tabular
        try:
            from core.tools import read_document
            text = read_document(path)
        except Exception as e:
            raise ValueError(f"Could not read PDF statement: {e}") from e
        # try CSV-like extraction
        if "," in text and "date" in text.lower():
            return parse_csv_transactions(text, account=account or path.stem)
        raise ValueError(
            "PDF statement parsed as text but no CSV table detected. "
            "Export transactions as CSV for best results."
        )
    raise ValueError(f"Unsupported statement format: {suffix}")


# ── analytics ─────────────────────────────────────────────────────

def monthly_summary(txns: List[Transaction]) -> Dict[str, Dict[str, float]]:
    """month → {income, expenses, net}"""
    months: Dict[str, Dict[str, float]] = {}
    for t in txns:
        month = (t.date or "")[:7]
        if not month:
            continue
        bucket = months.setdefault(month, {"income": 0.0, "expenses": 0.0, "net": 0.0})
        if t.amount >= 0:
            bucket["income"] += t.amount
        else:
            bucket["expenses"] += abs(t.amount)
        bucket["net"] = bucket["income"] - bucket["expenses"]
    return dict(sorted(months.items()))


def spending_by_category(txns: List[Transaction]) -> Dict[str, float]:
    cats: Dict[str, float] = {}
    for t in txns:
        if t.amount < 0:
            cats[t.category] = cats.get(t.category, 0.0) + abs(t.amount)
    return dict(sorted(cats.items(), key=lambda x: -x[1]))


def detect_recurring(txns: List[Transaction], min_count: int = 2) -> List[Dict[str, Any]]:
    """Group similar descriptions with similar amounts."""
    groups: Dict[str, List[Transaction]] = {}
    for t in txns:
        if t.amount >= 0:
            continue
        key = re.sub(r"\d+", "", t.description.lower())
        key = re.sub(r"\s+", " ", key).strip()[:40]
        groups.setdefault(key, []).append(t)
    recurring = []
    for key, items in groups.items():
        if len(items) < min_count:
            continue
        amounts = [abs(i.amount) for i in items]
        avg = sum(amounts) / len(amounts)
        if max(amounts) - min(amounts) > max(5.0, avg * 0.25):
            continue
        recurring.append({
            "description": items[0].description,
            "count": len(items),
            "avg_amount": round(avg, 2),
            "category": items[0].category,
            "dates": [i.date for i in sorted(items, key=lambda x: x.date)],
        })
    recurring.sort(key=lambda r: -r["avg_amount"])
    return recurring


def detect_anomalies(txns: List[Transaction]) -> List[Dict[str, Any]]:
    """Large one-offs, duplicates, fee spikes."""
    findings: List[Dict[str, Any]] = []
    expenses = [t for t in txns if t.amount < 0]
    if not expenses:
        return findings

    amounts = [abs(t.amount) for t in expenses]
    mean = sum(amounts) / len(amounts)
    # large one-off: > 3x mean and > 100
    for t in expenses:
        if abs(t.amount) > max(100.0, mean * 3):
            findings.append({
                "type": "large_expense",
                "date": t.date,
                "description": t.description,
                "amount": t.amount,
                "detail": f"Unusually large vs avg {mean:.2f}",
            })

    # duplicates: same date, description, amount
    seen: Dict[Tuple[str, str, float], int] = {}
    for t in expenses:
        key = (t.date, t.description.lower().strip(), round(t.amount, 2))
        seen[key] = seen.get(key, 0) + 1
    for key, count in seen.items():
        if count >= 2:
            findings.append({
                "type": "duplicate",
                "date": key[0],
                "description": key[1],
                "amount": key[2],
                "detail": f"Appears {count} times",
            })

    # subscription flag
    for t in expenses:
        if t.category == "subscription":
            findings.append({
                "type": "subscription",
                "date": t.date,
                "description": t.description,
                "amount": t.amount,
                "detail": "Subscription-like charge",
            })

    return findings


def recommendations(txns: List[Transaction]) -> List[str]:
    tips: List[str] = []
    by_cat = spending_by_category(txns)
    recurring = detect_recurring(txns)
    months = monthly_summary(txns)

    if by_cat.get("dining", 0) > 0 and by_cat.get("dining", 0) >= max(by_cat.values(), default=0) * 0.25:
        tips.append(
            f"Dining is a top expense ({by_cat['dining']:.2f}). "
            "Consider a weekly meal budget or fewer delivery orders."
        )
    subs = [r for r in recurring if r.get("category") == "subscription"]
    if not subs:
        subs = [r for r in recurring if "subscription" in (r.get("description") or "").lower()]
    if recurring:
        total_rec = sum(r["avg_amount"] for r in recurring[:8])
        tips.append(
            f"Detected {len(recurring)} recurring payments (~{total_rec:.2f}/cycle). "
            "Review ones you no longer use."
        )
    if months:
        nets = [v["net"] for v in months.values()]
        if nets and sum(1 for n in nets if n < 0) >= max(1, len(nets) // 2):
            tips.append("Cash flow is negative in multiple months — prioritize an emergency buffer.")
        avg_net = sum(nets) / len(nets)
        if avg_net > 0:
            tips.append(
                f"Average monthly surplus ~{avg_net:.2f}. "
                "Automate a transfer to savings on payday."
            )
    fees = by_cat.get("fees", 0)
    if fees > 20:
        tips.append(f"Bank/fees total {fees:.2f}. Check overdraft and ATM fee settings.")
    if not tips:
        tips.append("Keep exporting monthly statements so PEAR can track trends over time.")
    return tips


def transactions_to_searchable_text(txns: List[Transaction]) -> str:
    lines = ["Date | Amount | Category | Description"]
    for t in sorted(txns, key=lambda x: x.date):
        lines.append(f"{t.date} | {t.amount:+.2f} | {t.category} | {t.description}")
    return "\n".join(lines)
