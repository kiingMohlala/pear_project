"""
Memory Intelligence (v1.80) – scoring, consolidation, clustering, decay, preferences.
Compatible with Memory.sync_intelligence / memory_cleanup / memory_search / memory_stats.
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class MemoryPolicy:
    decay_half_life_days: float = 30.0
    archive_threshold: float = 0.15
    consolidate_threshold: float = 0.55
    max_active: int = 500
    dedupe_threshold: float = 0.9


@dataclass
class MemoryItem:
    id: str
    text: str
    source: str = ""
    category: str = "general"  # general | preference | fact | summary | archive | note | document
    importance: float = 0.5
    frequency: int = 1
    last_access: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def tokenize(text: str) -> List[str]:
    return [t for t in re.split(r"\W+", (text or "").lower()) if len(t) > 2]


def jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def content_hash(text: str) -> str:
    norm = re.sub(r"\s+", " ", (text or "").lower()).strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


PREF_PATTERNS = [
    r"\bi (?:prefer|like|love|hate|dislike|want|need)\b",
    r"\bmy (?:favorite|preferred|usual|default)\b",
    r"\bplease (?:always|never)\b",
    r"\bcall me\b",
]
FACT_PATTERNS = [
    r"\bmy name is\b",
    r"\bi (?:live|work|am) (?:in|at|a|an)\b",
    r"\btimezone\b",
    r"\bemail is\b",
]


class MemoryIntelligence:
    def __init__(self, memory: Any = None, policy: Optional[MemoryPolicy] = None, **kwargs):
        self.memory = memory
        self.policy = policy or MemoryPolicy(
            decay_half_life_days=kwargs.get("decay_half_life_days", 30.0),
            archive_threshold=kwargs.get("archive_threshold", 0.15),
            consolidate_threshold=kwargs.get("consolidate_threshold", 0.55),
            max_active=kwargs.get("max_active", 500),
        )
        self.items: Dict[str, MemoryItem] = {}
        self.feedback: Dict[str, float] = {}
        self._stats: Dict[str, int] = defaultdict(int)
        self.extracted_preferences: Dict[str, str] = {}

    def attach(self, memory: Any) -> None:
        self.memory = memory

    def _span(self, name: str, **attrs):
        try:
            from .tracing import get_tracer
            return get_tracer().span(name, kind="memory", **attrs)
        except Exception:
            from contextlib import nullcontext
            return nullcontext()

    # ── ingest adapters (Memory.sync_intelligence) ────────────────

    def ingest_note(self, note: Dict[str, Any]) -> MemoryItem:
        text = f"{note.get('title', '')}\n{note.get('body', note.get('text', ''))}".strip()
        return self.observe(text, source=f"note:{note.get('id', '')}", category="note")

    def ingest_document(self, doc: Dict[str, Any]) -> MemoryItem:
        text = f"{doc.get('name', '')}\n{(doc.get('text') or '')[:2000]}".strip()
        return self.observe(text, source=f"doc:{doc.get('id', doc.get('name', ''))}", category="document")

    def ingest_fact(self, fact: Any) -> MemoryItem:
        if isinstance(fact, dict):
            text = str(fact.get("fact") or fact.get("text") or fact)
            tags = fact.get("tags") or []
            cat = "preference" if "preference" in tags else "fact"
        else:
            text = str(fact)
            cat = "fact"
        return self.observe(text, source="fact", category=cat)

    def extract_preferences(self, utterances: List[str]) -> Dict[str, str]:
        found = {}
        for u in utterances:
            low = u.lower()
            if any(re.search(p, low) for p in PREF_PATTERNS):
                key = "pref_" + content_hash(u)[:8]
                found[key] = u[:200]
                self.extracted_preferences[key] = u[:200]
                self.observe(u, source="utterance", category="preference")
            m = re.search(r"my name is\s+([A-Za-z][A-Za-z\s\-]{1,40})", u, re.I)
            if m:
                found["name"] = m.group(1).strip()
                self.extracted_preferences["name"] = found["name"]
                if self.memory and hasattr(self.memory, "long_term"):
                    try:
                        self.memory.long_term.set_pref("name", found["name"])
                    except Exception:
                        pass
        self._stats["preferences_extracted"] += len(found)
        return found

    # ── core observe / score ──────────────────────────────────────

    def observe(
        self,
        text: str,
        *,
        source: str = "",
        category: str = "general",
        outcome_success: Optional[bool] = None,
    ) -> MemoryItem:
        text = (text or "").strip()
        if not text:
            return MemoryItem(id="empty", text="", importance=0.0)
        h = content_hash(text)
        for item in self.items.values():
            if item.metadata.get("hash") == h or jaccard(tokenize(item.text), tokenize(text)) > self.policy.dedupe_threshold:
                item.frequency += 1
                item.last_access = time.time()
                item.importance = self.score(item, outcome_success=outcome_success)
                self._stats["duplicates_merged"] += 1
                return item

        cat = category if category != "general" else self._infer_category(text)
        item = MemoryItem(
            id=f"mem_{uuid.uuid4().hex[:10]}",
            text=text,
            source=source,
            category=cat,
            frequency=1,
            metadata={"hash": h},
        )
        item.importance = self.score(item, outcome_success=outcome_success)
        self.items[item.id] = item
        self._stats["observed"] += 1

        if cat == "preference":
            self._store_pref(text)
        if cat == "fact":
            self._store_fact(text)

        if self.memory and hasattr(self.memory, "knowledge"):
            try:
                self.memory.knowledge.add_document(
                    name=f"memory:{item.category}:{item.id}",
                    text=text[:3000],
                    source_path=source or item.id,
                    metadata={"type": "memory_item", "category": item.category, "importance": item.importance, "id": item.id},
                )
            except Exception:
                pass
        return item

    def score(self, item: MemoryItem, *, outcome_success: Optional[bool] = None, now: Optional[float] = None) -> float:
        with self._span("memory.score", id=item.id):
            now = now or time.time()
            age_days = max(0.0, (now - item.created_at) / 86400.0)
            recency = 1.0 / (1.0 + age_days / max(self.policy.decay_half_life_days, 1.0))
            access_days = max(0.0, (now - item.last_access) / 86400.0)
            access_boost = 1.0 / (1.0 + access_days / 7.0)
            freq = min(1.0, 0.2 + 0.15 * item.frequency)
            cat_boost = {
                "preference": 0.25, "fact": 0.2, "summary": 0.15,
                "note": 0.05, "document": 0.05, "general": 0.0, "archive": -0.3,
            }.get(item.category, 0.0)
            fb = self.feedback.get(item.id, 0.0)
            outcome = 0.1 if outcome_success is True else (-0.1 if outcome_success is False else 0.0)
            raw = 0.35 * recency + 0.2 * access_boost + 0.2 * freq + cat_boost + 0.15 * fb + outcome
            return max(0.0, min(1.0, raw))

    def feedback_item(self, item_id: str, value: float) -> None:
        self.feedback[item_id] = max(-1.0, min(1.0, value))
        if item_id in self.items:
            self.items[item_id].importance = self.score(self.items[item_id])

    def _infer_category(self, text: str) -> str:
        low = text.lower()
        for pat in PREF_PATTERNS:
            if re.search(pat, low):
                return "preference"
        for pat in FACT_PATTERNS:
            if re.search(pat, low):
                return "fact"
        return "general"

    def _store_pref(self, text: str) -> None:
        key = "pref_" + content_hash(text)[:8]
        self.extracted_preferences[key] = text[:200]
        if self.memory and hasattr(self.memory, "long_term"):
            try:
                self.memory.long_term.set_pref(key, text[:200])
            except Exception:
                pass
        self._stats["preferences_extracted"] += 1

    def _store_fact(self, text: str) -> None:
        if self.memory and hasattr(self.memory, "long_term"):
            try:
                m = re.search(r"my name is\s+([A-Za-z][A-Za-z\s\-]{1,40})", text, re.I)
                if m:
                    self.memory.long_term.set_pref("name", m.group(1).strip())
                    self.extracted_preferences["name"] = m.group(1).strip()
                m = re.search(r"i live in\s+([A-Za-z][A-Za-z\s\-]{1,40})", text, re.I)
                if m:
                    self.memory.long_term.set_pref("location", m.group(1).strip())
            except Exception:
                pass
        self._stats["facts_extracted"] += 1

    # ── cluster / consolidate / archive ───────────────────────────

    def cluster(self, threshold: Optional[float] = None) -> List[List[str]]:
        with self._span("memory.cluster"):
            thr = threshold if threshold is not None else self.policy.consolidate_threshold
            active = [i for i in self.items.values() if i.category != "archive"]
            tokens = {i.id: tokenize(i.text) for i in active}
            clusters: List[List[str]] = []
            assigned = set()
            for item in sorted(active, key=lambda x: -x.importance):
                if item.id in assigned:
                    continue
                group = [item.id]
                assigned.add(item.id)
                for other in active:
                    if other.id in assigned:
                        continue
                    if jaccard(tokens[item.id], tokens[other.id]) >= thr:
                        group.append(other.id)
                        assigned.add(other.id)
                clusters.append(group)
            self._stats["clusters"] = len(clusters)
            return clusters

    def consolidate(self) -> List[MemoryItem]:
        with self._span("memory.consolidate"):
            clusters = self.cluster()
            summaries: List[MemoryItem] = []
            for group in clusters:
                if len(group) < 2:
                    continue
                members = [self.items[i] for i in group if i in self.items]
                if len(members) < 2:
                    continue
                joined = " | ".join(m.text[:120] for m in sorted(members, key=lambda x: -x.importance)[:5])
                summary_text = f"Consolidated ({len(members)} memories): {joined}"
                summary = MemoryItem(
                    id=f"mem_sum_{uuid.uuid4().hex[:8]}",
                    text=summary_text,
                    source="consolidation",
                    category="summary",
                    frequency=sum(m.frequency for m in members),
                    importance=max(m.importance for m in members),
                    metadata={"members": group, "hash": content_hash(summary_text)},
                )
                summary.importance = self.score(summary)
                self.items[summary.id] = summary
                for m in members:
                    m.category = "archive"
                    m.metadata["archived_into"] = summary.id
                summaries.append(summary)
                self._stats["consolidated"] += 1
            return summaries

    def apply_decay(self) -> int:
        now = time.time()
        updated = 0
        for item in self.items.values():
            if item.category == "archive":
                continue
            new_score = self.score(item, now=now)
            if abs(new_score - item.importance) > 0.01:
                item.importance = new_score
                updated += 1
        self._stats["decay_updates"] = updated
        return updated

    def archive_low_value(self) -> int:
        with self._span("memory.archive"):
            self.apply_decay()
            archived = 0
            for item in list(self.items.values()):
                if item.category in ("preference", "fact", "summary"):
                    continue
                if item.importance < self.policy.archive_threshold:
                    item.category = "archive"
                    archived += 1
            active = [i for i in self.items.values() if i.category != "archive"]
            if len(active) > self.policy.max_active:
                active.sort(key=lambda x: x.importance)
                for item in active[: len(active) - self.policy.max_active]:
                    item.category = "archive"
                    archived += 1
            self._stats["archived"] = self._stats.get("archived", 0) + archived
            return archived

    def cleanup(self) -> Dict[str, Any]:
        decayed = self.apply_decay()
        summaries = self.consolidate()
        archived = self.archive_low_value()
        return {
            "decayed": decayed,
            "summaries": len(summaries),
            "archived": archived,
            "active": sum(1 for i in self.items.values() if i.category != "archive"),
            "total": len(self.items),
        }

    def search(self, query: str, limit: int = 10) -> List[Any]:
        q_tokens = tokenize(query)
        scored: List[Tuple[float, MemoryItem]] = []
        for item in self.items.values():
            if item.category == "archive":
                continue
            overlap = jaccard(q_tokens, tokenize(item.text))
            score = 0.6 * overlap + 0.4 * item.importance
            if overlap > 0 or query.lower() in item.text.lower():
                item.last_access = time.time()
                scored.append((score, item))
        scored.sort(key=lambda x: -x[0])
        return [i for _, i in scored[:limit]]

    def stats(self) -> Dict[str, Any]:
        by_cat: Dict[str, int] = defaultdict(int)
        importances = []
        for i in self.items.values():
            by_cat[i.category] += 1
            if i.category != "archive":
                importances.append(i.importance)
        return {
            "total": len(self.items),
            "by_category": dict(by_cat),
            "avg_importance": round(sum(importances) / len(importances), 4) if importances else 0.0,
            "stats": dict(self._stats),
            "preferences": dict(self.extracted_preferences),
        }

    # aliases used by tests / orchestrator
    def memory_stats(self) -> Dict[str, Any]:
        return self.stats()

    def list_memories(self, category: Optional[str] = None, limit: int = 30) -> List[Dict[str, Any]]:
        items = list(self.items.values())
        if category:
            items = [i for i in items if i.category == category]
        items.sort(key=lambda x: -x.importance)
        return [i.to_dict() for i in items[:limit]]
