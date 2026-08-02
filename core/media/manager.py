"""
MediaManager – unified pipeline for audio, images, screenshots, PDF pages.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .base import MediaArtifact, MediaType, Transcript, VisionResult
from .speech import BaseSpeech, create_speech
from .vision import BaseVision, create_vision

if TYPE_CHECKING:
    from ..memory import KnowledgeStore


class MediaManager:
    def __init__(
        self,
        speech: Optional[BaseSpeech] = None,
        vision: Optional[BaseVision] = None,
        knowledge: Optional["KnowledgeStore"] = None,
        media_dir: Optional[Path] = None,
    ):
        self.speech = speech or create_speech()
        self.vision = vision or create_vision()
        self.knowledge = knowledge
        self.media_dir = Path(media_dir) if media_dir else Path.home() / "PEAR_Workspace" / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts: List[MediaArtifact] = []

    def _span(self, name: str, **attrs):
        try:
            from ..tracing import get_tracer
            return get_tracer().span(name, kind="tool", **attrs)
        except Exception:
            from contextlib import nullcontext
            return nullcontext()

    def _index_text(self, name: str, text: str, source: str, meta: Optional[Dict] = None) -> None:
        if not self.knowledge or not text.strip():
            return
        try:
            self.knowledge.add_document(
                name=name,
                text=text,
                source_path=source,
                metadata={"type": "media_extract", **(meta or {})},
            )
        except Exception:
            pass

    # ── speech ────────────────────────────────────────────────────

    def transcribe(self, audio_path: Path | str, **kwargs) -> Dict[str, Any]:
        path = Path(audio_path).expanduser()
        with self._span("media.transcribe", path=str(path), provider=self.speech.provider):
            if not path.exists():
                return {"ok": False, "error": f"Audio not found: {path}"}
            try:
                result = self.speech.transcribe(path, **kwargs)
            except Exception as e:
                # retry once with offline
                from .speech import OfflineSpeech
                result = OfflineSpeech().transcribe(path)
                result.metadata = {"fallback": str(e)}  # type: ignore
        art = MediaArtifact(
            id=f"media_{uuid.uuid4().hex[:10]}",
            type=MediaType.AUDIO,
            path=str(path),
            text=result.text,
            metadata={"provider": result.provider, "language": result.language},
        )
        self.artifacts.append(art)
        self._index_text(f"transcript:{path.name}", result.text, str(path), {"media": "audio"})
        return {"ok": True, "transcript": result.to_dict(), "artifact": art.to_dict()}

    # ── vision ────────────────────────────────────────────────────

    def ocr(self, image_path: Path | str, **kwargs) -> Dict[str, Any]:
        path = Path(image_path).expanduser()
        with self._span("media.ocr", path=str(path), provider=self.vision.provider):
            if not path.exists():
                return {"ok": False, "error": f"Image not found: {path}"}
            try:
                result = self.vision.ocr(path, **kwargs)
            except Exception as e:
                from .vision import OfflineVision
                result = OfflineVision().ocr(path)
                result.metadata["fallback"] = str(e)
        art = MediaArtifact(
            id=f"media_{uuid.uuid4().hex[:10]}",
            type=MediaType.IMAGE,
            path=str(path),
            text=result.text,
            metadata={"provider": result.provider, "mode": "ocr"},
        )
        self.artifacts.append(art)
        self._index_text(f"ocr:{path.name}", result.text or result.description, str(path), {"media": "ocr"})
        return {"ok": True, "vision": result.to_dict(), "artifact": art.to_dict()}

    def describe_image(self, image_path: Path | str, prompt: str = "", **kwargs) -> Dict[str, Any]:
        path = Path(image_path).expanduser()
        with self._span("media.describe", path=str(path), provider=self.vision.provider):
            if not path.exists():
                return {"ok": False, "error": f"Image not found: {path}"}
            try:
                result = self.vision.describe(path, prompt=prompt, **kwargs)
            except Exception as e:
                from .vision import OfflineVision
                result = OfflineVision().describe(path, prompt=prompt)
                result.metadata["fallback"] = str(e)
        art = MediaArtifact(
            id=f"media_{uuid.uuid4().hex[:10]}",
            type=MediaType.IMAGE,
            path=str(path),
            text=result.description or result.text,
            metadata={"provider": result.provider, "mode": "describe"},
        )
        self.artifacts.append(art)
        self._index_text(
            f"image:{path.name}",
            result.description or result.text,
            str(path),
            {"media": "describe"},
        )
        return {"ok": True, "vision": result.to_dict(), "artifact": art.to_dict()}

    def ingest_screenshot(self, image_path: Path | str, *, ocr: bool = True) -> Dict[str, Any]:
        path = Path(image_path).expanduser()
        with self._span("media.screenshot_ingest", path=str(path)):
            if not path.exists():
                return {"ok": False, "error": f"Screenshot not found: {path}"}
            text = ""
            if ocr:
                r = self.ocr(path)
                text = (r.get("vision") or {}).get("text") or ""
            art = MediaArtifact(
                id=f"media_{uuid.uuid4().hex[:10]}",
                type=MediaType.SCREENSHOT,
                path=str(path),
                text=text,
                metadata={"ocr": ocr},
            )
            self.artifacts.append(art)
            if text:
                self._index_text(f"screenshot:{path.name}", text, str(path), {"media": "screenshot"})
            return {"ok": True, "artifact": art.to_dict(), "text": text}

    def process(self, path: Path | str, media_type: Optional[str] = None) -> Dict[str, Any]:
        """Route a file to the right pipeline by extension or declared type."""
        path = Path(path).expanduser()
        suffix = path.suffix.lower()
        mt = (media_type or "").lower()
        if mt in ("audio",) or suffix in (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm"):
            return self.transcribe(path)
        if mt in ("screenshot",) or "screenshot" in path.name.lower():
            return self.ingest_screenshot(path)
        if mt in ("image", "ocr") or suffix in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"):
            return self.ocr(path)
        if suffix == ".pdf":
            # first page text via existing document reader when possible
            try:
                from ..tools import read_document
                text = read_document(path)
                art = MediaArtifact(
                    id=f"media_{uuid.uuid4().hex[:10]}",
                    type=MediaType.PDF_PAGE,
                    path=str(path),
                    text=text[:50000],
                    metadata={"pages": "all"},
                )
                self.artifacts.append(art)
                self._index_text(f"pdf:{path.name}", text[:50000], str(path), {"media": "pdf"})
                return {"ok": True, "artifact": art.to_dict(), "text": text[:5000]}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        return {"ok": False, "error": f"Unsupported media: {path}"}

    def status(self) -> Dict[str, Any]:
        return {
            "speech_provider": self.speech.provider,
            "vision_provider": self.vision.provider,
            "speech_available": self.speech.is_available(),
            "vision_available": self.vision.is_available(),
            "artifacts": len(self.artifacts),
            "media_dir": str(self.media_dir),
        }
