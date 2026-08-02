"""
Voice Assistant loop (v1.70) – wake-word, listen, transcribe, plan, speak.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from .media.speech import BaseSpeech, create_speech, OfflineSpeech
from .media.tts import BaseTTS, create_tts, OfflineTTS

if TYPE_CHECKING:
    from .orchestrator import Orchestrator


@dataclass
class VoiceSettings:
    wake_word: str = "hey pear"
    silence_timeout_s: float = 1.2
    max_listen_s: float = 12.0
    muted: bool = False
    auto_speak: bool = True
    language: str = "en"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VoiceTurn:
    id: str
    transcript: str
    reply: str
    wake_detected: bool
    latency_ms: Dict[str, float] = field(default_factory=dict)
    interrupted: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class VoiceAssistant:
    """
    Continuous conversation helper.

    In offline/CI mode, audio paths may be text sidecars; wake-word is
    matched against transcript text rather than a neural detector.
    """

    def __init__(
        self,
        orchestrator: Optional["Orchestrator"] = None,
        speech: Optional[BaseSpeech] = None,
        tts: Optional[BaseTTS] = None,
        settings: Optional[VoiceSettings] = None,
        media_dir: Optional[Path] = None,
    ):
        self.orch = orchestrator
        self.speech = speech or create_speech("offline")
        self.tts = tts or create_tts("offline")
        self.settings = settings or VoiceSettings()
        self.media_dir = Path(media_dir) if media_dir else Path.home() / "PEAR_Workspace" / "voice"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.history: List[VoiceTurn] = []
        self._listening = False

    def _span(self, name: str, **attrs):
        try:
            from .tracing import get_tracer
            return get_tracer().span(name, kind="voice", **attrs)
        except Exception:
            from contextlib import nullcontext
            return nullcontext()

    # ── wake word ─────────────────────────────────────────────────

    def detect_wake_word(self, text: str) -> bool:
        if not text:
            return False
        phrase = self.settings.wake_word.lower().strip()
        norm = re.sub(r"\s+", " ", text.lower())
        return phrase in norm

    # ── listen / transcribe ───────────────────────────────────────

    def listen_file(self, audio_path: Path | str) -> Dict[str, Any]:
        """Transcribe a recorded utterance (streaming abstraction = chunked offline)."""
        if self.settings.muted:
            return {"ok": False, "error": "muted"}
        path = Path(audio_path).expanduser()
        with self._span("voice.listen", path=str(path)):
            t0 = time.time()
            # simulate VAD / silence window metadata
            listen_meta = {
                "silence_timeout_s": self.settings.silence_timeout_s,
                "max_listen_s": self.settings.max_listen_s,
            }
        with self._span("voice.transcribe"):
            t1 = time.time()
            if hasattr(self.orch, "media") and self.orch is not None:
                result = self.orch.media.transcribe(path)
                text = ((result.get("transcript") or {}).get("text") or "") if result.get("ok") else ""
                ok = result.get("ok", False)
            else:
                tr = self.speech.transcribe(path)
                text, ok = tr.text, True
            t2 = time.time()
        return {
            "ok": ok,
            "text": text,
            "latency_ms": {
                "listen": (t1 - t0) * 1000,
                "transcribe": (t2 - t1) * 1000,
            },
            "meta": listen_meta,
        }

    def handle_utterance(self, text: str, *, require_wake: bool = False) -> VoiceTurn:
        """Route text (from STT or typed) through planner and optional TTS."""
        t0 = time.time()
        wake = self.detect_wake_word(text)
        content = text
        if wake:
            # strip wake phrase
            content = re.sub(
                re.escape(self.settings.wake_word),
                "",
                text,
                flags=re.I,
            ).strip(" ,.-")
        if require_wake and not wake:
            turn = VoiceTurn(
                id=f"vt_{uuid.uuid4().hex[:8]}",
                transcript=text,
                reply="",
                wake_detected=False,
            )
            self.history.append(turn)
            return turn

        with self._span("voice.plan", text=content[:80]):
            t1 = time.time()
            reply = ""
            if self.orch is not None and content:
                result = self.orch.route(content)
                reply = result.get("reply") or result.get("error") or str(result)
            elif content:
                reply = f"(no orchestrator) heard: {content}"
            else:
                reply = "Yes?"
            t2 = time.time()

        interrupted = False
        with self._span("voice.speak", chars=len(reply)):
            if self.settings.auto_speak and reply and not self.settings.muted:
                audio = self.tts.speak(reply, output_dir=self.media_dir)
                interrupted = audio.interrupted
            t3 = time.time()

        turn = VoiceTurn(
            id=f"vt_{uuid.uuid4().hex[:8]}",
            transcript=text,
            reply=reply,
            wake_detected=wake or not require_wake,
            latency_ms={
                "plan": (t2 - t1) * 1000,
                "speak": (t3 - t2) * 1000,
                "total": (t3 - t0) * 1000,
            },
            interrupted=interrupted,
        )
        self.history.append(turn)
        return turn

    def process_audio(self, audio_path: Path | str, *, require_wake: bool = False) -> Dict[str, Any]:
        listened = self.listen_file(audio_path)
        if not listened.get("ok"):
            return listened
        turn = self.handle_utterance(listened.get("text") or "", require_wake=require_wake)
        return {
            "ok": True,
            "turn": turn.to_dict(),
            "transcript": turn.transcript,
            "reply": turn.reply,
            "latency_ms": {**listened.get("latency_ms", {}), **turn.latency_ms},
        }

    def barge_in(self) -> None:
        """Interrupt current TTS (barge-in)."""
        self.tts.interrupt()

    def mute(self) -> None:
        self.settings.muted = True
        self.barge_in()

    def unmute(self) -> None:
        self.settings.muted = False

    def status(self) -> Dict[str, Any]:
        return {
            "settings": self.settings.to_dict(),
            "speech_provider": getattr(self.speech, "provider", "?"),
            "tts_provider": getattr(self.tts, "provider", "?"),
            "turns": len(self.history),
            "muted": self.settings.muted,
        }
