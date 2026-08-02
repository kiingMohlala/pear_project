"""Voice assistant regression tests (v1.70)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.voice import VoiceAssistant, VoiceSettings
from core.media.speech import OfflineSpeech
from core.media.tts import OfflineTTS, create_tts
from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from agents import PersonalAgent
from evaluation.engine import EvaluationEngine


def test_wake_word():
    v = VoiceAssistant(speech=OfflineSpeech(), tts=OfflineTTS(), settings=VoiceSettings(wake_word="hey pear"))
    assert v.detect_wake_word("Hey PEAR set a timer")
    assert not v.detect_wake_word("hey google")


def test_process_audio_routes():
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        orch = Orchestrator(memory=Memory(session_id="v1", persist_dir=td_path), llm=EchoLLM())
        orch.register(PersonalAgent(llm=EchoLLM()), default=True)
        voice = VoiceAssistant(orchestrator=orch, speech=OfflineSpeech(), tts=OfflineTTS(), media_dir=td_path)
        audio = td_path / "a.wav"
        audio.write_bytes(b"x")
        (td_path / "a.txt").write_text("note: buy milk")
        r = voice.process_audio(audio)
        assert r["ok"]
        assert "milk" in (r.get("reply") or "").lower() or "note" in (r.get("reply") or "").lower() or r.get("transcript")


def test_mute_and_settings():
    v = VoiceAssistant(speech=OfflineSpeech(), tts=OfflineTTS())
    v.mute()
    assert v.settings.muted
    assert v.listen_file("nope.wav").get("error") == "muted"
    v.unmute()
    assert not v.settings.muted


def test_tts_interrupt():
    tts = OfflineTTS()
    tts.interrupt()
    out = tts.speak("hello world from pear voice assistant")
    assert out.path


def test_orchestrator_has_voice():
    orch = Orchestrator(memory=Memory(session_id="v2"), llm=EchoLLM())
    assert hasattr(orch, "voice")
    assert "wake_word" in orch.voice.status()["settings"]


def test_eval_suite():
    eng = EvaluationEngine()
    report = eng.run(suites=["voice"], save_history=False, compare_baseline=False)
    assert report.suites["voice"].success_rate >= 0.8


if __name__ == "__main__":
    test_wake_word()
    print("  ✓ wake word")
    test_process_audio_routes()
    print("  ✓ process audio")
    test_mute_and_settings()
    print("  ✓ mute")
    test_tts_interrupt()
    print("  ✓ tts")
    test_orchestrator_has_voice()
    print("  ✓ orchestrator")
    test_eval_suite()
    print("  ✓ eval")
    print("All v1.70 voice tests passed.")
