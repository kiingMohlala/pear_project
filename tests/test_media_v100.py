"""Multimodal foundation regression tests (v1.00)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.media import (
    MediaManager,
    create_speech,
    create_vision,
    OfflineSpeech,
    OfflineVision,
)
from core.memory import Memory, KnowledgeStore
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from core.workflow import Workflow, WorkflowStep, StepType, WorkflowStatus
from core.embeddings import NullEmbeddings
from core.vector_store import VectorStore
from agents import PersonalAgent


def test_offline_speech_sidecar():
    with tempfile.TemporaryDirectory() as td:
        audio = Path(td) / "note.wav"
        audio.write_bytes(b"RIFF....WAVE")  # stub bytes
        sidecar = Path(td) / "note.txt"
        sidecar.write_text("hello from sidecar transcript")
        sp = OfflineSpeech()
        t = sp.transcribe(audio)
        assert "hello from sidecar" in t.text


def test_offline_ocr_sidecar():
    with tempfile.TemporaryDirectory() as td:
        img = Path(td) / "scan.png"
        img.write_bytes(b"\x89PNG\r\n")
        (Path(td) / "scan.png.txt").write_text("Invoice total R1200")
        v = OfflineVision()
        r = v.ocr(img)
        assert "Invoice" in r.text


def test_media_manager_indexes_knowledge():
    with tempfile.TemporaryDirectory() as td:
        ks = KnowledgeStore(embeddings=NullEmbeddings(), vector_store=VectorStore())
        mm = MediaManager(
            speech=OfflineSpeech(),
            vision=OfflineVision(),
            knowledge=ks,
            media_dir=Path(td),
        )
        img = Path(td) / "doc.png"
        img.write_bytes(b"\x89PNG")
        (Path(td) / "doc.png.txt").write_text("Confidential NDA clause about liability")
        r = mm.ocr(img)
        assert r["ok"]
        hits = ks.search("liability NDA", limit=3)
        assert hits


def test_transcribe_pipeline():
    with tempfile.TemporaryDirectory() as td:
        mm = MediaManager(speech=OfflineSpeech(), vision=OfflineVision(), media_dir=Path(td))
        audio = Path(td) / "a.wav"
        audio.write_bytes(b"x")
        (Path(td) / "a.txt").write_text("meeting notes tomorrow")
        r = mm.transcribe(audio)
        assert r["ok"]
        assert "meeting" in r["transcript"]["text"]


def test_screenshot_ingest():
    with tempfile.TemporaryDirectory() as td:
        mm = MediaManager(speech=OfflineSpeech(), vision=OfflineVision(), media_dir=Path(td))
        shot = Path(td) / "screenshot_1.png"
        shot.write_bytes(b"\x89PNG")
        (Path(td) / "screenshot_1.png.txt").write_text("Desktop window title bar")
        r = mm.ingest_screenshot(shot)
        assert r["ok"]
        assert "Desktop" in r.get("text", "")


def test_create_providers_auto_offline():
    sp = create_speech("offline")
    vis = create_vision("offline")
    assert sp.provider == "offline"
    assert vis.provider == "offline"


def test_orchestrator_media_status():
    orch = Orchestrator(memory=Memory(session_id="m1"), llm=EchoLLM())
    st = orch.media.status()
    assert "speech_provider" in st
    assert "vision_provider" in st


def test_workflow_media_step():
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        mem = Memory(session_id="m2", persist_dir=td_path)
        orch = Orchestrator(memory=mem, llm=EchoLLM())
        orch.register(PersonalAgent(llm=EchoLLM()), default=True)
        img = td_path / "x.png"
        img.write_bytes(b"\x89PNG")
        (td_path / "x.png.txt").write_text("workflow ocr text")
        # force offline providers
        from core.media import OfflineSpeech, OfflineVision
        orch.media.speech = OfflineSpeech()
        orch.media.vision = OfflineVision()
        wf = Workflow(
            name="ocr_demo",
            steps=[
                WorkflowStep(
                    name="ocr",
                    type=StepType.MEDIA,
                    media_action="ocr",
                    media_path=str(img),
                    save_as="ocr_out",
                ),
            ],
        )
        orch.workflows.register(wf)
        run = orch.workflows.start("ocr_demo")
        assert run.status == WorkflowStatus.COMPLETED
        assert run.context.get("ocr_out", {}).get("ok")


def test_permissions_media_actions():
    from core.permissions import Permissions
    p = Permissions()
    assert p.can("ocr")
    assert p.can("transcribe")
    p.set_policy("transcribe", "never")
    assert not p.can("transcribe")


if __name__ == "__main__":
    test_offline_speech_sidecar()
    print("  ✓ speech sidecar")
    test_offline_ocr_sidecar()
    print("  ✓ ocr sidecar")
    test_media_manager_indexes_knowledge()
    print("  ✓ knowledge index")
    test_transcribe_pipeline()
    print("  ✓ transcribe")
    test_screenshot_ingest()
    print("  ✓ screenshot")
    test_create_providers_auto_offline()
    print("  ✓ providers")
    test_orchestrator_media_status()
    print("  ✓ orchestrator")
    test_workflow_media_step()
    print("  ✓ workflow")
    test_permissions_media_actions()
    print("  ✓ permissions")
    print("All v1.00 media tests passed.")
