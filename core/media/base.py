"""
Multimodal abstractions (v1.00) – speech, vision, unified media pipeline.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class MediaType(str, Enum):
    AUDIO = "audio"
    IMAGE = "image"
    PDF_PAGE = "pdf_page"
    SCREENSHOT = "screenshot"
    TEXT = "text"


@dataclass
class MediaArtifact:
    id: str
    type: MediaType
    path: Optional[str] = None
    text: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value if isinstance(self.type, MediaType) else self.type
        return d


@dataclass
class Transcript:
    text: str
    language: str = "en"
    confidence: float = 0.0
    segments: List[Dict[str, Any]] = field(default_factory=list)
    provider: str = ""
    duration_s: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VisionResult:
    text: str = ""
    description: str = ""
    labels: List[str] = field(default_factory=list)
    provider: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class BaseSpeech(ABC):
    provider: str = "base"

    @abstractmethod
    def transcribe(self, audio_path: Path, **kwargs) -> Transcript:
        ...

    def is_available(self) -> bool:
        return True


class BaseVision(ABC):
    provider: str = "base"

    @abstractmethod
    def ocr(self, image_path: Path, **kwargs) -> VisionResult:
        ...

    @abstractmethod
    def describe(self, image_path: Path, prompt: str = "", **kwargs) -> VisionResult:
        ...

    def is_available(self) -> bool:
        return True
