"""Regression tests for v0.33 background jobs & scheduler."""

from __future__ import annotations

import sys
import time
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.job import Job, JobStatus, JobPriority, ScheduleSpec
from core.job_manager import JobManager
from core.events import EventBus, EventType
from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from agents import PersonalAgent


def _mgr(tmp: Path = None, runner=None, workers=1) -> JobManager:
    path = None
    if tmp is not None:
        path = Path(tmp) / "jobs.sqlite"
    return JobManager(
        events=EventBus(),
        persist_path=path,
        runner=runner or (lambda job: {"ok": True, "reply": f"done:{job.objective}"}),
        poll_interval=0.05,
        max_workers=workers,
    )


def test_queue_priority_order():
    m = _mgr()
    low = m.enqueue("low work", priority=JobPriority.LOW)
    high = m.enqueue("high work", priority=JobPriority.HIGH)
    normal = m.enqueue("normal work", priority=JobPriority.NORMAL)
    q = m.queue()
    assert q[0].id == high.id
    assert q[-1].id == low.id


def test_execute_and_complete():
    events = EventBus()
    m = JobManager(
        events=events,
        runner=lambda job: {"ok": True, "reply": "x"},
        poll_interval=0.05,
    )
    job = m.enqueue("hello")
    done = m.run_once()
    assert done is not None
    assert done.status == JobStatus.COMPLETED
    assert done.result and done.result["ok"]
    types = [e.type for e in events.history]
    assert EventType.JOB_CREATED in types
    assert EventType.JOB_QUEUED in types
    assert EventType.JOB_STARTED in types
    assert EventType.JOB_COMPLETED in types


def test_cancel_before_run():
    m = _mgr()
    job = m.enqueue("never")
    m.cancel(job.id)
    assert m.get(job.id).status == JobStatus.CANCELLED
    assert m.run_once() is None


def test_pause_resume():
    m = _mgr()
    job = m.enqueue("paused work")
    m.pause(job.id)
    assert m.get(job.id).status == JobStatus.PAUSED
    assert m.queue() == []
    m.resume(job.id)
    assert m.get(job.id).status == JobStatus.QUEUED
    done = m.run_once()
    assert done.status == JobStatus.COMPLETED


def test_retry_on_failure():
    attempts = {"n": 0}

    def flaky(job):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise RuntimeError("boom")
        return {"ok": True, "reply": "recovered"}

    m = _mgr(runner=flaky)
    job = m.enqueue("flaky", max_attempts=3)
    m.run_once()
    assert m.get(job.id).status == JobStatus.RETRYING
    # force due
    with m._lock:
        m.get(job.id).scheduled_at = None
    m.run_once()
    assert m.get(job.id).status == JobStatus.COMPLETED
    assert attempts["n"] == 2


def test_schedule_once_future():
    m = _mgr()
    future = time.time() + 0.3
    job = m.schedule("later", when=future)
    assert m.run_once(timeout=0.05) is None  # not due yet
    time.sleep(0.35)
    done = m.run_once(timeout=1.0)
    assert done is not None and done.id == job.id
    assert done.status == JobStatus.COMPLETED


def test_schedule_interval_reschedules():
    m = _mgr()
    job = m.schedule("tick", interval_s=0.2)
    m.run_once(timeout=1.0)
    # next occurrence should be queued
    time.sleep(0.05)
    jobs = [j for j in m.list_jobs() if j.objective == "tick"]
    assert len(jobs) >= 2  # original completed + next queued


def test_persistence_restart_recovery():
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)

        def runner(job):
            return {"ok": True, "reply": "persisted"}

        m1 = _mgr(td_path, runner=runner)
        job = m1.enqueue("survive me")
        # Simulate crash mid-run
        with m1._lock:
            job.status = JobStatus.RUNNING
            m1._save_job(job)

        m2 = _mgr(td_path, runner=runner)
        recovered = m2.get(job.id)
        assert recovered is not None
        assert recovered.status == JobStatus.QUEUED  # demoted from RUNNING
        done = m2.run_once()
        assert done.status == JobStatus.COMPLETED


def test_concurrent_workers():
    started = []
    lock = __import__("threading").Lock()

    def slow(job):
        with lock:
            started.append(job.id)
        time.sleep(0.15)
        return {"ok": True, "reply": job.id}

    m = JobManager(
        events=EventBus(),
        runner=slow,
        poll_interval=0.02,
        max_workers=2,
    )
    a = m.enqueue("a")
    b = m.enqueue("b")
    m.start()
    deadline = time.time() + 3
    while time.time() < deadline:
        if m.get(a.id).status == JobStatus.COMPLETED and m.get(b.id).status == JobStatus.COMPLETED:
            break
        time.sleep(0.05)
    m.stop()
    assert m.get(a.id).status == JobStatus.COMPLETED
    assert m.get(b.id).status == JobStatus.COMPLETED
    # Both should have been running (overlap) with 2 workers
    assert len(started) == 2


def test_orchestrator_submit_job():
    with tempfile.TemporaryDirectory() as td:
        mem = Memory(session_id="jobs", persist_dir=Path(td))
        orch = Orchestrator(memory=mem, llm=EchoLLM())
        orch.register(PersonalAgent(llm=EchoLLM()), default=True)
        r = orch.submit_job("note: background note")
        assert r["ok"] and r.get("job_id")
        job_id = r["job_id"]
        deadline = time.time() + 5
        while time.time() < deadline:
            j = orch.jobs.get(job_id)
            if j and j.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                break
            time.sleep(0.05)
        orch.jobs.stop()
        j = orch.jobs.get(job_id)
        assert j is not None and j.status == JobStatus.COMPLETED, j
        assert len(orch.memory.list_notes()) >= 1


def test_retry_emits_queued():
    events = EventBus()
    m = JobManager(
        events=events,
        runner=lambda job: (_ for _ in ()).throw(RuntimeError("fail"))
        if False else (_ for _ in ()).throw(RuntimeError("fail")),
        poll_interval=0.05,
    )
    # Always fail once then allow retry path via max_attempts=1 so it ends FAILED
    def always_fail(job):
        raise RuntimeError("fail")
    m.runner = always_fail
    job = m.enqueue("x", max_attempts=1)
    m.run_once()
    assert m.get(job.id).status == JobStatus.FAILED
    events.history.clear()
    m.retry(job.id)
    types = [e.type for e in events.history]
    assert EventType.JOB_QUEUED in types
    assert m.get(job.id).status in (JobStatus.RETRYING, JobStatus.QUEUED)


def test_schedule_spec_daily():
    spec = ScheduleSpec(kind="daily", hour=3)
    nxt = spec.compute_next(after=time.time())
    assert nxt is not None and nxt > time.time()


if __name__ == "__main__":
    test_queue_priority_order()
    print("  ✓ priority order")
    test_execute_and_complete()
    print("  ✓ execute")
    test_cancel_before_run()
    print("  ✓ cancel")
    test_pause_resume()
    print("  ✓ pause/resume")
    test_retry_on_failure()
    print("  ✓ retry")
    test_schedule_once_future()
    print("  ✓ schedule once")
    test_schedule_interval_reschedules()
    print("  ✓ interval")
    test_persistence_restart_recovery()
    print("  ✓ persistence recovery")
    test_concurrent_workers()
    print("  ✓ concurrent workers")
    test_orchestrator_submit_job()
    print("  ✓ orchestrator submit")
    test_retry_emits_queued()
    print("  ✓ retry emits queued")
    test_schedule_spec_daily()
    print("  ✓ daily spec")
    print("All v0.33 job tests passed.")
