"""
Legal domain helpers: document typing, clause parsing, concept tagging, comparison.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, asdict
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple


# ── models ────────────────────────────────────────────────────────

@dataclass
class Clause:
    id: str
    number: str
    title: str
    text: str
    concepts: List[str] = field(default_factory=list)
    obligations: List[str] = field(default_factory=list)
    parties: List[str] = field(default_factory=list)
    dates: List[str] = field(default_factory=list)
    governing_law: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Clause":
        return cls(
            id=str(d.get("id") or f"cl_{uuid.uuid4().hex[:8]}"),
            number=str(d.get("number") or ""),
            title=str(d.get("title") or ""),
            text=str(d.get("text") or ""),
            concepts=list(d.get("concepts") or []),
            obligations=list(d.get("obligations") or []),
            parties=list(d.get("parties") or []),
            dates=list(d.get("dates") or []),
            governing_law=d.get("governing_law"),
        )


DOC_TYPE_SIGNALS: List[Tuple[str, List[str]]] = [
    ("nda", ["non-disclosure", "nondisclosure", "confidential information", "receiving party", "disclosing party"]),
    ("employment", ["employee", "employer", "job title", "at-will", "compensation", "duties of employment"]),
    ("lease", ["landlord", "tenant", "premises", "lease term", "security deposit", "rent"]),
    ("purchase", ["purchase agreement", "buyer", "seller", "purchase price", "bill of sale"]),
    ("terms_of_service", ["terms of service", "terms of use", "acceptable use", "user content"]),
    ("msa", ["master service", "statement of work", "service provider", "sow"]),
    ("sla", ["service level", "uptime", "response time", "service credits"]),
]


CONCEPT_PATTERNS: List[Tuple[str, str]] = [
    ("confidentiality", r"\bconfidential(?:ity)?\b|\bnon[- ]?disclosure\b"),
    ("termination", r"\bterminat(?:e|ion)\b|\bexpir(?:e|ation)\b"),
    ("indemnity", r"\bindemnif(?:y|ication)\b|\bhold\s+harmless\b"),
    ("liability", r"\bliability\b|\bconsequential\s+damages\b|\blimitation\s+of\s+liability\b"),
    ("payment", r"\bpayment\b|\bfees?\b|\binvoice\b|\bcompensation\b|\bsalary\b"),
    ("intellectual_property", r"\bintellectual\s+property\b|\bwork\s+for\s+hire\b|\bcopyright\b|\bpatent\b"),
    ("dispute_resolution", r"\barbitration\b|\bmediation\b|\bjurisdiction\b|\bgoverning\s+law\b|\bvenue\b"),
    ("renewal", r"\brenew(?:al|s|ed)?\b|\bautomatic(?:ally)?\s+renew"),
    ("non_compete", r"\bnon[- ]?compete\b|\brestrictive\s+covenant\b"),
    ("non_solicit", r"\bnon[- ]?solicit"),
    ("assignment", r"\bassignment\b|\bassign\s+this\s+agreement\b"),
    ("force_majeure", r"\bforce\s+majeure\b"),
    ("data_protection", r"\bGDPR\b|\bPOPIA\b|\bdata\s+protection\b|\bpersonal\s+data\b"),
]


EXPECTED_CLAUSES_BY_TYPE: Dict[str, List[str]] = {
    "nda": ["confidentiality", "term", "return", "governing law", "definition"],
    "employment": ["duties", "compensation", "termination", "confidentiality"],
    "lease": ["rent", "term", "premises", "deposit", "maintenance"],
    "generic": ["termination", "governing law", "liability"],
}


SECTION_RE = re.compile(
    r"(?m)^(?:\s*)("
    r"(?:\d{1,2}(?:\.\d+)*[\.\)]\s+)|"           # 1. / 1.1 / 1)
    r"(?:[A-Z]\.\s+)|"                             # A.
    r"(?:(?:SECTION|ARTICLE|CLAUSE)\s+\d+[\.\:]?\s+)"
    r")"
    r"([A-Z][A-Za-z0-9 \-/&',]{2,80}?)\s*$"
)

DATE_RE = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}|"
    r"\d{1,2}\s+(?:days?|months?|years?))\b",
    re.I,
)

PARTY_RE = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9&.'-]+(?:\s+[A-Z][A-Za-z0-9&.'-]+){0,4})\s+"
    r"\((?:the\s+)?\"?(?:Disclosing Party|Receiving Party|Company|Employee|Landlord|Tenant|Buyer|Seller|Client|Provider)\"?\)",
)

OBLIGATION_RE = re.compile(
    r"\b(?:shall|must|agrees?\s+to|is\s+required\s+to|will\s+not|shall\s+not)\b[^.|]{10,160}",
    re.I,
)


def detect_document_type(text: str, name: str = "") -> str:
    blob = f"{name}\n{text[:3000]}".lower()
    best, best_hits = "generic", 0
    for dtype, signals in DOC_TYPE_SIGNALS:
        hits = sum(1 for s in signals if s in blob)
        if hits > best_hits:
            best, best_hits = dtype, hits
    return best


def tag_concepts(text: str) -> List[str]:
    found = []
    for concept, pat in CONCEPT_PATTERNS:
        if re.search(pat, text, re.I):
            found.append(concept)
    return found


def extract_clauses(text: str) -> List[Clause]:
    """Split document into numbered/headed clauses."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    headers: List[Tuple[int, str, str]] = []  # line idx, number, title
    for i, line in enumerate(lines):
        m = SECTION_RE.match(line.strip())
        if m:
            number = m.group(1).strip()
            title = m.group(2).strip()
            headers.append((i, number, title))

    clauses: List[Clause] = []
    if not headers:
        # Single blob
        body = text.strip()
        if body:
            clauses.append(_build_clause("1", "Document", body))
        return clauses

    for idx, (line_i, number, title) in enumerate(headers):
        start = line_i + 1
        end = headers[idx + 1][0] if idx + 1 < len(headers) else len(lines)
        body = "\n".join(lines[start:end]).strip()
        # include title line content if body thin
        full = f"{title}\n{body}".strip()
        clauses.append(_build_clause(number, title, full))
    return clauses


def _build_clause(number: str, title: str, text: str) -> Clause:
    concepts = tag_concepts(text)
    obligations = [m.group(0).strip() for m in OBLIGATION_RE.finditer(text)][:5]
    parties = [m.group(0).strip() for m in PARTY_RE.finditer(text)][:5]
    dates = list({m.group(0) for m in DATE_RE.finditer(text)})[:8]
    gov = None
    if re.search(r"governing\s+law", text, re.I):
        m = re.search(r"governed by (?:the )?laws? of ([^.]+)", text, re.I)
        if m:
            gov = m.group(1).strip()
    return Clause(
        id=f"cl_{uuid.uuid4().hex[:8]}",
        number=number,
        title=title,
        text=text,
        concepts=concepts,
        obligations=obligations,
        parties=parties,
        dates=dates,
        governing_law=gov,
    )


def find_missing_clauses(doc_type: str, clauses: List[Clause]) -> List[str]:
    expected = EXPECTED_CLAUSES_BY_TYPE.get(doc_type, EXPECTED_CLAUSES_BY_TYPE["generic"])
    blob = " ".join(f"{c.title} {c.text}" for c in clauses).lower()
    missing = []
    for exp in expected:
        if exp.lower() not in blob and not any(exp.replace(" ", "_") in c.concepts for c in clauses):
            # soft match tokens
            tokens = exp.lower().split()
            if not all(t in blob for t in tokens):
                missing.append(exp)
    return missing


def compare_clause_lists(
    a: List[Clause],
    b: List[Clause],
    threshold: float = 0.55,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Compare two clause lists → added / removed / modified.
    Matching is by title similarity then text ratio.
    """
    used_b = set()
    modified = []
    removed = []
    added = []

    def score(x: Clause, y: Clause) -> float:
        title_r = SequenceMatcher(None, x.title.lower(), y.title.lower()).ratio()
        text_r = SequenceMatcher(None, x.text.lower()[:2000], y.text.lower()[:2000]).ratio()
        return 0.4 * title_r + 0.6 * text_r

    for ca in a:
        best_j, best_s = -1, 0.0
        for j, cb in enumerate(b):
            if j in used_b:
                continue
            s = score(ca, cb)
            if s > best_s:
                best_s, best_j = s, j
        if best_j < 0 or best_s < threshold:
            removed.append({"clause": ca.to_dict(), "reason": "no match in B"})
        else:
            used_b.add(best_j)
            cb = b[best_j]
            text_r = SequenceMatcher(None, ca.text.lower(), cb.text.lower()).ratio()
            if text_r < 0.92:
                modified.append({
                    "before": ca.to_dict(),
                    "after": cb.to_dict(),
                    "similarity": round(text_r, 3),
                })
    for j, cb in enumerate(b):
        if j not in used_b:
            added.append({"clause": cb.to_dict(), "reason": "no match in A"})

    return {"added": added, "removed": removed, "modified": modified}


def clauses_to_searchable_text(clauses: List[Clause], doc_name: str, doc_type: str) -> str:
    parts = [f"Document: {doc_name}", f"Type: {doc_type}", ""]
    for c in clauses:
        concepts = ",".join(c.concepts)
        parts.append(f"[{c.number}] {c.title} (concepts: {concepts})")
        parts.append(c.text)
        parts.append("")
    return "\n".join(parts)
