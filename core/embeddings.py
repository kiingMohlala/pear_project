"""
Embedding providers for PEAR semantic retrieval (v0.32).

Agents never depend on a specific provider — KnowledgeStore calls create_embeddings().
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence


def _l2_normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


class BaseEmbeddings(ABC):
    provider: str = "base"
    dimensions: int = 0

    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        ...

    def embed_query(self, text: str) -> List[float]:
        return self.embed([text])[0]

    def is_available(self) -> bool:
        return True


class NullEmbeddings(BaseEmbeddings):
    """
    Deterministic pseudo-embeddings from token hashing.
    Always available — enables a degraded semantic path without models.
    """

    provider = "null"
    dimensions = 64

    def embed(self, texts: List[str]) -> List[List[float]]:
        out: List[List[float]] = []
        for text in texts:
            vec = [0.0] * self.dimensions
            tokens = [t for t in text.lower().split() if t]
            if not tokens:
                tokens = ["_empty_"]
            for tok in tokens:
                h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
                idx = h % self.dimensions
                sign = 1.0 if (h >> 8) & 1 else -1.0
                vec[idx] += sign
            out.append(_l2_normalize(vec))
        return out


class OllamaEmbeddings(BaseEmbeddings):
    """Local embeddings via Ollama /api/embeddings."""

    provider = "ollama"

    def __init__(
        self,
        model: str = "nomic-embed-text",
        base_url: str = "http://localhost:11434",
        dimensions: int = 768,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.dimensions = dimensions

    def embed(self, texts: List[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for text in texts:
            payload = json.dumps({"model": self.model, "prompt": text}).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}/api/embeddings",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            vec = body.get("embedding") or []
            if not vec:
                raise RuntimeError("Ollama returned empty embedding")
            self.dimensions = len(vec)
            vectors.append(_l2_normalize([float(x) for x in vec]))
        return vectors

    def is_available(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False


class SentenceTransformerEmbeddings(BaseEmbeddings):
    """Optional local models via sentence-transformers."""

    provider = "sentence_transformers"

    def __init__(self, model: str = "all-MiniLM-L6-v2"):
        self.model_name = model
        self._model = None
        self.dimensions = 384

    def _load(self):
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer  # type: ignore

        self._model = SentenceTransformer(self.model_name)
        # probe dims
        probe = self._model.encode(["probe"], normalize_embeddings=True)
        self.dimensions = len(probe[0])

    def embed(self, texts: List[str]) -> List[List[float]]:
        self._load()
        assert self._model is not None
        arr = self._model.encode(texts, normalize_embeddings=True)
        return [[float(x) for x in row] for row in arr]

    def is_available(self) -> bool:
        try:
            import sentence_transformers  # noqa: F401

            return True
        except Exception:
            return False


class OpenAIEmbeddings(BaseEmbeddings):
    provider = "openai"

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        base_url: str = "https://api.openai.com/v1",
        dimensions: int = 1536,
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.dimensions = dimensions

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        payload = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        data = sorted(body.get("data") or [], key=lambda d: d.get("index", 0))
        vectors = []
        for item in data:
            vec = [float(x) for x in item.get("embedding") or []]
            self.dimensions = len(vec)
            vectors.append(_l2_normalize(vec))
        return vectors

    def is_available(self) -> bool:
        return bool(self.api_key)


def create_embeddings(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    **kwargs,
) -> BaseEmbeddings:
    """
    Env:
      PEAR_EMBED_PROVIDER = ollama | sentence_transformers | openai | null
      PEAR_EMBED_MODEL
      OLLAMA_HOST
      OPENAI_API_KEY
    """
    provider = (provider or os.environ.get("PEAR_EMBED_PROVIDER", "auto")).lower()
    model = model or os.environ.get("PEAR_EMBED_MODEL")

    def try_ollama() -> Optional[BaseEmbeddings]:
        emb = OllamaEmbeddings(
            model=model or "nomic-embed-text",
            base_url=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            **{k: v for k, v in kwargs.items() if k in ("dimensions",)},
        )
        return emb if emb.is_available() else None

    def try_st() -> Optional[BaseEmbeddings]:
        emb = SentenceTransformerEmbeddings(model=model or "all-MiniLM-L6-v2")
        return emb if emb.is_available() else None

    def try_openai() -> Optional[BaseEmbeddings]:
        emb = OpenAIEmbeddings(model=model or "text-embedding-3-small", **kwargs)
        return emb if emb.is_available() else None

    if provider == "null":
        return NullEmbeddings()
    if provider == "ollama":
        return try_ollama() or NullEmbeddings()
    if provider in ("sentence_transformers", "st", "local"):
        return try_st() or NullEmbeddings()
    if provider == "openai":
        return try_openai() or NullEmbeddings()

    # auto: prefer local real models, then null
    for factory in (try_st, try_ollama, try_openai):
        emb = factory()
        if emb is not None:
            return emb
    return NullEmbeddings()
