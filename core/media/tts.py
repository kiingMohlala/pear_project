"""Text-to-speech providers (v1.70) – swappable with offline fallback."""

from __future__ import annotations

import os
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class SpeechAudio:
    path: Optional[str]
    text: str
    provider: str
    duration_s: float = 0.0
    interrupted: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseTTS(ABC):
    provider: str = "base"

    @abstractmethod
    def speak(self, text: str, *, output_dir: Optional[Path] = None) -> SpeechAudio:
        ...

    def interrupt(self) -> None:
        """Best-effort barge-in support."""

    def is_available(self) -> bool:
        return True


class OfflineTTS(BaseTTS):
    """Writes a sidecar transcript; no real audio (CI-safe)."""

    provider = "offline"

    def __init__(self):
        self._interrupted = False

    def speak(self, text: str, *, output_dir: Optional[Path] = None) -> SpeechAudio:
        self._interrupted = False
        out_dir = Path(output_dir) if output_dir else Path.home() / "PEAR_Workspace" / "voice"
        out_dir.mkdir(parents=True, exist_ok=True)
        # estimate duration ~12 chars/sec
        duration = max(0.3, len(text) / 12.0)
        path = out_dir / f"tts_{uuid.uuid4().hex[:8]}.txt"
        path.write_text(text, encoding="utf-8")
        # simulate playback loop with interrupt checks
        steps = max(1, int(duration / 0.05))
        for _ in range(steps):
            if self._interrupted:
                return SpeechAudio(
                    path=str(path), text=text, provider=self.provider,
                    duration_s=duration, interrupted=True,
                )
            time.sleep(0.0)  # no real sleep in offline for tests
        return SpeechAudio(path=str(path), text=text, provider=self.provider, duration_s=duration)

    def interrupt(self) -> None:
        self._interrupted = True


class SystemTTS(BaseTTS):
    """Uses OS `say` (macOS) or `espeak` (Linux) when present."""

    provider = "system"

    def __init__(self):
        self._proc = None

    def is_available(self) -> bool:
        import shutil
        return bool(shutil.which("say") or shutil.which("espeak") or shutil.which("espeak-ng"))

    def speak(self, text: str, *, output_dir: Optional[Path] = None) -> SpeechAudio:
        import shutil
        import subprocess
        out_dir = Path(output_dir) if output_dir else Path.home() / "PEAR_Workspace" / "voice"
        out_dir.mkdir(parents=True, exist_ok=True)
        wav = out_dir / f"tts_{uuid.uuid4().hex[:8]}.wav"
        try:
            if shutil.which("say"):
                subprocess.run(["say", "-o", str(wav), text], check=False, timeout=60)
            elif shutil.which("espeak-ng"):
                subprocess.run(["espeak-ng", "-w", str(wav), text], check=False, timeout=60)
            elif shutil.which("espeak"):
                subprocess.run(["espeak", "-w", str(wav), text], check=False, timeout=60)
            duration = max(0.3, len(text) / 12.0)
            return SpeechAudio(path=str(wav), text=text, provider=self.provider, duration_s=duration)
        except Exception as e:
            return SpeechAudio(path=None, text=text, provider=self.provider, metadata={"error": str(e)})

    def interrupt(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass


def create_tts(provider: Optional[str] = None) -> BaseTTS:
    provider = (provider or os.environ.get("PEAR_TTS_PROVIDER", "auto")).lower()
    if provider == "offline":
        return OfflineTTS()
    if provider == "system":
        s = SystemTTS()
        return s if s.is_available() else OfflineTTS()
    s = SystemTTS()
    if s.is_available():
        return s
    return OfflineTTS()
