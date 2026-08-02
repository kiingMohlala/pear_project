"""
Memory layers for PEAR.

Working Memory   – current conversation (session)
Long-Term Memory – user preferences, facts that survive sessions
Knowledge Store  – documents, PDFs, notes, extracted knowledge

v0.1 keeps the three layers in one class for simplicity;
persistence is still JSON-based. Later each layer can move to
its own backend (SQLite, vector store, etc.).
"""

from __future__ import annotations

from .memory_intelligence import MemoryIntelligence, MemoryPolicy

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .embeddings import BaseEmbeddings, create_embeddings, cosine_similarity
from .vector_store import VectorStore


@dataclass
class Message:
    role: str          # "user" | "assistant" | "system"
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Layer implementations ─────────────────────────────────────────

class WorkingMemory:
    """Current conversation only. Cleared with /clear or new session."""

    def __init__(self):
        self.messages: List[Message] = []

    def add(self, role: str, content: str, **metadata) -> None:
        self.messages.append(Message(role=role, content=content, metadata=metadata))

    def get_history(self, limit: Optional[int] = None) -> List[Message]:
        if limit is None:
            return list(self.messages)
        return self.messages[-limit:]

    def clear(self) -> None:
        self.messages.clear()

    def as_prompt_context(self, limit: int = 20) -> str:
        lines = [f"{m.role.upper()}: {m.content}" for m in self.get_history(limit)]
        return "\n".join(lines)


class LongTermMemory:
    """
    User preferences and durable facts.
    Example keys: preferred_name, timezone, default_folder, ...
    """

    def __init__(self):
        self.preferences: Dict[str, Any] = {}
        self.facts: List[Dict[str, Any]] = []

    def set_pref(self, key: str, value: Any) -> None:
        self.preferences[key] = value

    def get_pref(self, key: str, default: Any = None) -> Any:
        return self.preferences.get(key, default)

    def add_fact(self, fact: str, tags: Optional[List[str]] = None) -> None:
        self.facts.append({
            "fact": fact,
            "tags": tags or [],
            "created_at": time.time(),
        })

    def list_facts(self) -> List[Dict[str, Any]]:
        return list(self.facts)



class KnowledgeStore:
    """
    Documents, notes, and retrieved knowledge with hybrid semantic search (v0.32).

    Public API (stable for agents):
      add_document / add_note / search / build_context / list_* / latest_document
    """

    def __init__(
        self,
        embeddings: Optional[BaseEmbeddings] = None,
        vector_store: Optional[VectorStore] = None,
        chunk_size: int = 500,
        chunk_overlap: int = 80,
    ):
        self.notes: List[Dict[str, Any]] = []
        self.documents: List[Dict[str, Any]] = []
        self.embeddings: BaseEmbeddings = embeddings or create_embeddings()
        self.vectors = vector_store or VectorStore()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    # ── notes ─────────────────────────────────────────────────────

    def add_note(self, title: str, body: str, tags: Optional[List[str]] = None) -> Dict[str, Any]:
        note = {
            "id": f"note_{int(time.time() * 1000)}",
            "title": title,
            "body": body,
            "tags": tags or [],
            "created_at": time.time(),
        }
        self.notes.append(note)
        self._index_text(
            text=f"{title}\n{body}",
            metadata={
                "type": "note",
                "doc_id": note["id"],
                "name": title,
                "chunk_index": 0,
                "tags": note["tags"],
                "created_at": note["created_at"],
            },
            record_id=f"{note['id']}_0",
        )
        return note

    def list_notes(self) -> List[Dict[str, Any]]:
        return list(self.notes)

    # ── documents ─────────────────────────────────────────────────

    def add_document(
        self,
        name: str,
        text: str,
        source_path: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        doc = {
            "id": f"doc_{int(time.time() * 1000)}",
            "name": name,
            "text": text,
            "source_path": source_path,
            "metadata": metadata or {},
            "created_at": time.time(),
        }
        self.documents.append(doc)
        self._index_document(doc)
        return doc

    def list_documents(self) -> List[Dict[str, Any]]:
        return list(self.documents)

    def latest_document(self) -> Optional[Dict[str, Any]]:
        return self.documents[-1] if self.documents else None

    def _index_document(self, doc: Dict[str, Any]) -> None:
        # Drop prior chunks for this doc id (re-ingest)
        self.vectors.delete_by_metadata(doc_id=doc["id"])
        chunks = chunk_text(doc.get("text") or "", self.chunk_size, self.chunk_overlap)
        for i, chunk in enumerate(chunks):
            self._index_text(
                text=chunk,
                metadata={
                    "type": "document",
                    "doc_id": doc["id"],
                    "name": doc.get("name"),
                    "source_path": doc.get("source_path"),
                    "chunk_index": i,
                    "tags": list((doc.get("metadata") or {}).get("tags") or []),
                    "created_at": doc.get("created_at"),
                },
                record_id=f"{doc['id']}_{i}",
            )

    def _index_text(
        self,
        text: str,
        metadata: Dict[str, Any],
        record_id: Optional[str] = None,
    ) -> None:
        try:
            vec = self.embeddings.embed([text])[0]
        except Exception:
            # fall back to null-style local hash embed without swapping provider
            from .embeddings import NullEmbeddings
            vec = NullEmbeddings().embed([text])[0]
        self.vectors.add(text=text, vector=vec, metadata=metadata, id=record_id)

    # ── search ────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        include_notes: bool = True,
        include_documents: bool = True,
        rerank: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Hybrid retrieval: semantic top-k + keyword fallback/boost.
        Return shape matches pre-v0.32 callers (type, id, title, snippet, score).
        """
        try:
            from .tracing import get_tracer
            _span_cm = get_tracer().span("retrieval.search", kind="retrieval", query=query[:120])
            _span_cm.__enter__()
        except Exception:
            _span_cm = None
        try:
            return self._search_inner(
                query,
                limit=limit,
                include_notes=include_notes,
                include_documents=include_documents,
                rerank=rerank,
            )
        finally:
            if _span_cm is not None:
                try:
                    _span_cm.__exit__(None, None, None)
                except Exception:
                    pass

    def _search_inner(
        self,
        query: str,
        *,
        limit: int = 5,
        include_notes: bool = True,
        include_documents: bool = True,
        rerank: bool = True,
    ) -> List[Dict[str, Any]]:
        semantic = self._semantic_search(
            query,
            limit=max(limit * 3, 10),
            include_notes=include_notes,
            include_documents=include_documents,
        )
        keyword = self._keyword_search(
            query,
            limit=max(limit * 3, 10),
            include_notes=include_notes,
            include_documents=include_documents,
        )
        merged = self._merge_hits(semantic, keyword)
        if rerank:
            merged = self._rerank(query, merged)
        return merged[:limit]

    def _semantic_search(
        self,
        query: str,
        *,
        limit: int,
        include_notes: bool,
        include_documents: bool,
    ) -> List[Dict[str, Any]]:
        if not query.strip() or len(self.vectors) == 0:
            return []
        try:
            qvec = self.embeddings.embed_query(query)
        except Exception:
            from .embeddings import NullEmbeddings
            qvec = NullEmbeddings().embed_query(query)

        results = self.vectors.search(qvec, top_k=limit * 2)
        hits: List[Dict[str, Any]] = []
        for rec, score in results:
            meta = rec.metadata or {}
            rtype = meta.get("type", "document")
            if rtype == "note" and not include_notes:
                continue
            if rtype == "document" and not include_documents:
                continue
            snippet = rec.text[:500]
            q_tokens = [t for t in query.lower().split() if len(t) > 2]
            body_l = rec.text.lower()
            for t in sorted(q_tokens, key=len, reverse=True):
                pos = body_l.find(t)
                if pos >= 0:
                    start = max(0, pos - 120)
                    snippet = rec.text[start : start + 480]
                    break
            hits.append({
                "type": rtype,
                "id": meta.get("doc_id") or rec.id,
                "title": meta.get("name") or "",
                "snippet": snippet,
                "score": float(score),
                "source": "semantic",
                "chunk_index": meta.get("chunk_index"),
                "source_path": meta.get("source_path"),
                "metadata": meta,
            })
        return hits[:limit]

    def _keyword_search(
        self,
        query: str,
        *,
        limit: int,
        include_notes: bool,
        include_documents: bool,
    ) -> List[Dict[str, Any]]:
        q = query.lower().strip()
        stop = {"the", "and", "for", "are", "is", "this", "that", "with", "from", "what", "how", "who", "when", "where"}
        tokens = [
            t for t in q.replace("?", " ").replace(",", " ").split()
            if len(t) > 2 and t not in stop
        ]
        if not tokens:
            tokens = [q] if q else []

        hits: List[Dict[str, Any]] = []

        # Prefer searching indexed chunks so snippets align with the right section
        for rec in self.vectors._records.values():
            meta = rec.metadata or {}
            rtype = meta.get("type", "document")
            if rtype == "note" and not include_notes:
                continue
            if rtype == "document" and not include_documents:
                continue
            blob = f"{meta.get('name', '')} {rec.text}".lower()
            score = sum(1 for t in tokens if t in blob)
            if score <= 0:
                continue
            # Snippet centered on first matching token
            snippet = rec.text[:500]
            body = rec.text
            body_l = body.lower()
            for t in sorted(tokens, key=len, reverse=True):
                pos = body_l.find(t)
                if pos >= 0:
                    start = max(0, pos - 120)
                    snippet = body[start : start + 480]
                    break
            hits.append({
                "type": rtype,
                "id": meta.get("doc_id") or rec.id,
                "title": meta.get("name") or "",
                "snippet": snippet,
                "score": float(score),
                "source": "keyword",
                "chunk_index": meta.get("chunk_index"),
                "source_path": meta.get("source_path"),
                "metadata": meta,
            })

        # Fallback: whole notes/docs if index empty
        if not hits and include_notes:
            for note in self.notes:
                blob = f"{note.get('title', '')} {note.get('body', '')}".lower()
                score = sum(1 for t in tokens if t in blob)
                if score > 0:
                    hits.append({
                        "type": "note",
                        "id": note.get("id"),
                        "title": note.get("title", ""),
                        "snippet": (note.get("body") or "")[:400],
                        "score": float(score),
                        "source": "keyword",
                    })
        if not hits and include_documents:
            for doc in self.documents:
                blob = f"{doc.get('name', '')} {doc.get('text', '')}".lower()
                score = sum(1 for t in tokens if t in blob)
                if score > 0:
                    snippet = (doc.get("text") or "")[:400]
                    for t in tokens:
                        pos = blob.find(t)
                        if pos >= 0:
                            start = max(0, pos - 80)
                            snippet = (doc.get("text") or "")[start:start + 400]
                            break
                    hits.append({
                        "type": "document",
                        "id": doc.get("id"),
                        "title": doc.get("name", ""),
                        "snippet": snippet,
                        "score": float(score),
                        "source": "keyword",
                        "source_path": doc.get("source_path"),
                    })

        hits.sort(key=lambda h: h.get("score", 0), reverse=True)
        return hits[:limit]

    def _merge_hits(
        self,
        semantic: List[Dict[str, Any]],
        keyword: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Combine lists. Key = (type, id, chunk_index).
        Semantic scores in ~[0,1]; keyword scores are raw counts → normalize.
        """
        max_kw = max((h.get("score", 0) for h in keyword), default=1) or 1
        combined: Dict[tuple, Dict[str, Any]] = {}

        for h in semantic:
            key = (h.get("type"), h.get("id"), h.get("chunk_index"))
            entry = dict(h)
            entry["semantic_score"] = float(h.get("score") or 0)
            entry["keyword_score"] = 0.0
            entry["score"] = entry["semantic_score"]
            combined[key] = entry

        for h in keyword:
            key = (h.get("type"), h.get("id"), h.get("chunk_index"))
            kw_norm = float(h.get("score") or 0) / float(max_kw)
            if key in combined:
                combined[key]["keyword_score"] = kw_norm
                # hybrid: prefer semantic, boost with keyword
                # When using NullEmbeddings, lean harder on keywords
                sem_w, kw_w = (0.35, 0.65) if self.embeddings.provider == "null" else (0.7, 0.3)
                combined[key]["score"] = (
                    sem_w * combined[key].get("semantic_score", 0) + kw_w * kw_norm
                )
                combined[key]["source"] = "hybrid"
            else:
                entry = dict(h)
                entry["semantic_score"] = 0.0
                entry["keyword_score"] = kw_norm
                # Pure keyword still competitive under null embeddings
                entry["score"] = (0.8 if self.embeddings.provider == "null" else 0.35) * kw_norm
                combined[key] = entry

        hits = list(combined.values())
        hits.sort(key=lambda h: h.get("score", 0), reverse=True)
        return hits

    def _rerank(self, query: str, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Lightweight lexical re-rank on snippet overlap (no extra model)."""
        tokens = [t for t in query.lower().split() if len(t) > 2]
        if not tokens:
            return hits
        for h in hits:
            snippet = (h.get("snippet") or "").lower()
            overlap = sum(1 for t in tokens if t in snippet)
            h["score"] = float(h.get("score") or 0) + 0.05 * overlap
        hits.sort(key=lambda h: h.get("score", 0), reverse=True)
        return hits

    def build_context(self, query: str, max_chars: int = 6000) -> str:
        """Assemble retrieved snippets into a prompt-ready context block."""
        hits = self.search(query, limit=5)
        if not hits:
            # fall back to latest document head if nothing matched
            latest = self.latest_document()
            if latest and latest.get("text"):
                return (
                    f"[Document: {latest.get('name')}]\n"
                    + (latest["text"][:max_chars])
                )
            return ""

        parts: List[str] = []
        used = 0
        for h in hits:
            header = h.get("title") or h.get("id") or "hit"
            src = h.get("source", "")
            block = f"[{h.get('type', 'doc')}: {header} | {src}]\n{h.get('snippet', '')}"
            if used + len(block) > max_chars:
                remain = max_chars - used
                if remain > 100:
                    parts.append(block[:remain] + "...")
                break
            parts.append(block)
            used += len(block) + 2
        return "\n\n".join(parts)


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 80) -> List[str]:
    """
    Approximate token chunks using whitespace words (~1 token ~ 1 word for planning).
    """
    words = text.split()
    if not words:
        return []
    if len(words) <= chunk_size:
        return [" ".join(words)]

    chunks: List[str] = []
    step = max(1, chunk_size - overlap)
    i = 0
    while i < len(words):
        piece = words[i : i + chunk_size]
        if not piece:
            break
        chunks.append(" ".join(piece))
        if i + chunk_size >= len(words):
            break
        i += step
    return chunks



class Memory:
    """
    Facade that exposes the three layers and handles persistence.
    Agents and the planner interact with this object.
    """

    def __init__(self, session_id: str = "default", persist_dir: Optional[Path] = None):
        self.session_id = session_id
        self.persist_dir = Path(persist_dir) if persist_dir else None

        self.working = WorkingMemory()
        self.long_term = LongTermMemory()
        self.knowledge = KnowledgeStore()
        self.intelligence = MemoryIntelligence(memory=None)
        self.intelligence.attach(self)

        if self.persist_dir:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            self._load()

    # ── Convenience shims (backward-compatible with earlier code) ─

    def add(self, role: str, content: str, **metadata) -> None:
        self.working.add(role, content, **metadata)
        self._save()

    def get_history(self, limit: Optional[int] = None) -> List[Message]:
        return self.working.get_history(limit)

    def clear_session(self) -> None:
        self.working.clear()
        self._save()

    def as_prompt_context(self, limit: int = 20) -> str:
        return self.working.as_prompt_context(limit)

    def add_note(self, title: str, body: str, tags: Optional[List[str]] = None) -> None:
        self.knowledge.add_note(title, body, tags)
        self._save()

    def list_notes(self) -> List[Dict[str, Any]]:
        return self.knowledge.list_notes()

    # ── Persistence ───────────────────────────────────────────────


    # ── memory intelligence (v1.80) ───────────────────────────────

    def sync_intelligence(self) -> int:
        """Pull notes/documents/facts into the intelligence index."""
        n = 0
        for note in self.knowledge.list_notes():
            self.intelligence.ingest_note(note)
            n += 1
        for doc in self.knowledge.list_documents():
            self.intelligence.ingest_document(doc)
            n += 1
        for fact in self.long_term.list_facts():
            self.intelligence.ingest_fact(fact)
            n += 1
        # preferences as items
        for k, v in (self.long_term.preferences or {}).items():
            self.intelligence.ingest_fact({"fact": f"preference {k}={v}", "tags": ["preference"]})
            n += 1
        return n

    def memory_cleanup(self) -> dict:
        self.sync_intelligence()
        # extract preferences from recent user messages
        try:
            utts = [m.content for m in self.working.get_history(50) if getattr(m, "role", "") == "user"]
            self.intelligence.extract_preferences(utts)
        except Exception:
            pass
        return self.intelligence.cleanup()

    def memory_search(self, query: str, limit: int = 10) -> list:
        self.sync_intelligence()
        return self.intelligence.search(query, limit=limit)

    def memory_stats(self) -> dict:
        self.sync_intelligence()
        return self.intelligence.stats()

    def _path(self) -> Optional[Path]:
        if not self.persist_dir:
            return None
        return self.persist_dir / f"{self.session_id}.json"

    def _save(self) -> None:
        path = self._path()
        if not path:
            return
        data = {
            "session_id": self.session_id,
            "working": [m.to_dict() for m in self.working.messages],
            "long_term": {
                "preferences": self.long_term.preferences,
                "facts": self.long_term.facts,
            },
            "knowledge": {
                "notes": self.knowledge.notes,
                # Store document metadata only (full text can be large)
                "documents": [
                    {k: v for k, v in d.items() if k != "text"} | {"text_length": len(d.get("text", ""))}
                    for d in self.knowledge.documents
                ],
                # Keep full text of the most recent doc for summarization
                "latest_doc_text": (
                    self.knowledge.documents[-1]["text"]
                    if self.knowledge.documents else None
                ),
                "latest_doc_name": (
                    self.knowledge.documents[-1]["name"]
                    if self.knowledge.documents else None
                ),
            },
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load(self) -> None:
        path = self._path()
        if not path or not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))

            self.working.messages = [
                Message(**m) for m in data.get("working", data.get("messages", []))
            ]

            lt = data.get("long_term", {})
            self.long_term.preferences = lt.get("preferences", {})
            self.long_term.facts = lt.get("facts", [])

            ks = data.get("knowledge", {})
            self.knowledge.notes = ks.get("notes", data.get("notes", []))
            # Re-hydrate latest document text if present
            if ks.get("latest_doc_text"):
                self.knowledge.documents.append({
                    "id": "restored",
                    "name": ks.get("latest_doc_name", "restored"),
                    "text": ks["latest_doc_text"],
                    "source_path": None,
                    "metadata": {},
                    "created_at": time.time(),
                })
        except Exception:
            self.working.messages = []
            self.knowledge.notes = []
