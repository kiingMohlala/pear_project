"""Speech providers: Whisper local, optional cloud, offline fallback."""

from __future__ import annotations

import os
import wave
from pathlib import Path
from typing import Optional

from .base import BaseSpeech, Transcript


class OfflineSpeech(BaseSpeech):
    """Fallback when no ASR model is available."""

    provider = "offline"

    def transcribe(self, audio_path: Path, **kwargs) -> Transcript:
        path = Path(audio_path)
        note = f"[offline speech] No ASR model available for {path.name}."
        # If a sidecar .txt exists, treat it as pre-transcribed
        sidecar = path.with_suffix(path.suffix + ".txt")
        if not sidecar.exists():
            sidecar = path.with_suffix(".txt")
        if sidecar.exists():
            text = sidecar.read_text(encoding="utf-8", errors="replace").strip()
            return Transcript(text=text, confidence=1.0, provider=self.provider)
        return Transcript(text=note, confidence=0.0, provider=self.provider)

    def is_available(self) -> bool:
        return True


class WhisperSpeech(BaseSpeech):
    provider = "whisper"

    def __init__(self, model: str = "base"):
        self.model_name = model
        self._model = None

    def _load(self):
        if self._model is not None:
            return
        import whisper  # type: ignore
        self._model = whisper.load_model(self.model_name)

    def is_available(self) -> bool:
        try:
            import whisper  # noqa: F401
            return True
        except Exception:
            return False

    def transcribe(self, audio_path: Path, **kwargs) -> Transcript:
        self._load()
        assert self._model is not None
        result = self._model.transcribe(str(audio_path), **{k: v for k, v in kwargs.items() if k in ("language",)})
        segments = [
            {"start": s.get("start"), "end": s.get("end"), "text": s.get("text")}
            for s in (result.get("segments") or [])
        ]
        return Transcript(
            text=(result.get("text") or "").strip(),
            language=result.get("language") or "en",
            confidence=0.8,
            segments=segments,
            provider=self.provider,
        )


class CloudSpeech(BaseSpeech):
    """Optional OpenAI-compatible audio transcription API."""

    provider = "cloud"

    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://api.openai.com/v1", model: str = "whisper-1"):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.model = model

    def is_available(self) -> bool:
        return bool(self.api_key)

    def transcribe(self, audio_path: Path, **kwargs) -> Transcript:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        import json
        import urllib.request
        # Minimal multipart is heavy in stdlib — fall back message if requests missing
        try:
            import requests  # type: ignore
            with open(audio_path, "rb") as f:
                resp = requests.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    files={"file": f},
                    data={"model": self.model},
                    timeout=120,
                )
            resp.raise_for_status()
            data = resp.json()
            return Transcript(text=data.get("text", ""), provider=self.provider, confidence=0.85)
        except ImportError:
            return Transcript(
                text=f"[cloud speech] Install requests to use API for {Path(audio_path).name}",
                provider=self.provider,
                confidence=0.0,
            )


def create_speech(provider: Optional[str] = None) -> BaseSpeech:
    provider = (provider or os.environ.get("PEAR_SPEECH_PROVIDER", "auto")).lower()
    if provider == "offline":
        return OfflineSpeech()
    if provider == "whisper":
        w = WhisperSpeech()
        return w if w.is_available() else OfflineSpeech()
    if provider == "cloud":
        c = CloudSpeech()
        return c if c.is_available() else OfflineSpeech()
    # auto
    for cls in (WhisperSpeech, CloudSpeech):
        inst = cls()
        if inst.is_available():
            return inst
    return OfflineSpeech()
