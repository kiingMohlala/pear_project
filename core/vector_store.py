"""
Lightweight vector store for PEAR (v0.32).

Default backend: in-memory + optional SQLite persistence of vectors/metadata.
FAISS is used when installed; otherwise pure-Python cosine search.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .embeddings import cosine_similarity


@dataclass
class VectorRecord:
    id: str
    vector: List[float]
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "vector": self.vector,
            "text": self.text,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "VectorRecord":
        return cls(
            id=d["id"],
            vector=list(d.get("vector") or []),
            text=d.get("text") or "",
            metadata=dict(d.get("metadata") or {}),
        )


class VectorStore:
    """
    Pluggable top-k similarity index.

    Methods: add, update, delete, search, clear, save, load.
    """

    def __init__(self, persist_path: Optional[Path] = None):
        self.persist_path = Path(persist_path) if persist_path else None
        self._records: Dict[str, VectorRecord] = {}
        self._faiss_index = None
        self._faiss_ids: List[str] = []
        if self.persist_path and self.persist_path.exists():
            self.load()

    # ── mutations ─────────────────────────────────────────────────

    def add(
        self,
        *,
        text: str,
        vector: Sequence[float],
        metadata: Optional[Dict[str, Any]] = None,
        id: Optional[str] = None,
    ) -> str:
        rid = id or f"vec_{uuid.uuid4().hex[:12]}"
        self._records[rid] = VectorRecord(
            id=rid,
            vector=[float(x) for x in vector],
            text=text,
            metadata=metadata or {},
        )
        self._invalidate_faiss()
        return rid

    def update(
        self,
        id: str,
        *,
        text: Optional[str] = None,
        vector: Optional[Sequence[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if id not in self._records:
            raise KeyError(id)
        rec = self._records[id]
        if text is not None:
            rec.text = text
        if vector is not None:
            rec.vector = [float(x) for x in vector]
        if metadata is not None:
            rec.metadata = metadata
        self._invalidate_faiss()

    def delete(self, id: str) -> None:
        self._records.pop(id, None)
        self._invalidate_faiss()

    def delete_by_metadata(self, **match: Any) -> int:
        """Delete records whose metadata contains all match key/values."""
        to_del = []
        for rid, rec in self._records.items():
            if all(rec.metadata.get(k) == v for k, v in match.items()):
                to_del.append(rid)
        for rid in to_del:
            del self._records[rid]
        if to_del:
            self._invalidate_faiss()
        return len(to_del)

    def clear(self) -> None:
        self._records.clear()
        self._invalidate_faiss()

    # ── search ────────────────────────────────────────────────────

    def search(
        self,
        query_vector: Sequence[float],
        *,
        top_k: int = 5,
        filter: Optional[Dict[str, Any]] = None,
    ) -> List[Tuple[VectorRecord, float]]:
        q = [float(x) for x in query_vector]
        candidates = list(self._records.values())
        if filter:
            candidates = [
                r for r in candidates
                if all(r.metadata.get(k) == v for k, v in filter.items())
            ]
        if not candidates:
            return []

        scored = [
            (rec, cosine_similarity(q, rec.vector))
            for rec in candidates
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def __len__(self) -> int:
        return len(self._records)

    # ── persistence (SQLite) ──────────────────────────────────────

    def save(self, path: Optional[Path] = None) -> None:
        path = Path(path) if path else self.persist_path
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vectors (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    vector TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    updated_at REAL
                )
                """
            )
            conn.execute("DELETE FROM vectors")
            for rec in self._records.values():
                conn.execute(
                    "INSERT INTO vectors (id, text, vector, metadata, updated_at) VALUES (?,?,?,?,?)",
                    (
                        rec.id,
                        rec.text,
                        json.dumps(rec.vector),
                        json.dumps(rec.metadata),
                        time.time(),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def load(self, path: Optional[Path] = None) -> None:
        path = Path(path) if path else self.persist_path
        if not path or not path.exists():
            return
        conn = sqlite3.connect(str(path))
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vectors (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    vector TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    updated_at REAL
                )
                """
            )
            rows = conn.execute("SELECT id, text, vector, metadata FROM vectors").fetchall()
            self._records.clear()
            for rid, text, vec_json, meta_json in rows:
                self._records[rid] = VectorRecord(
                    id=rid,
                    text=text,
                    vector=json.loads(vec_json),
                    metadata=json.loads(meta_json),
                )
            self._invalidate_faiss()
        finally:
            conn.close()

    def _invalidate_faiss(self) -> None:
        self._faiss_index = None
        self._faiss_ids = []
