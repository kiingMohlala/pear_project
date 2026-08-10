"""
JobManager – persistent queue, worker loop, scheduler (v0.33).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from .job import Job, JobStatus, JobPriority, PRIORITY_ORDER, ScheduleSpec
from .events import EventBus, EventType

if TYPE_CHECKING:
    pass

# Signature: (job) -> result dict
JobRunner = Callable[[Job], Dict[str, Any]]


class JobManager:
    def __init__(
        self,
        *,
        events: Optional[EventBus] = None,
        persist_path: Optional[Path] = None,
        runner: Optional[JobRunner] = None,
        poll_interval: float = 0.25,
        max_workers: int = 1,
        tracer: Optional[Any] = None,
    ):
        self.events = events or EventBus()
        self.persist_path = Path(persist_path) if persist_path else None
        self.runner = runner
        self.poll_interval = poll_interval
        self.max_workers = max(1, max_workers)
        self.tracer = tracer  # PEAR 3.1 Gate 1: owning orchestrator's tracer, if any

        self._jobs: Dict[str, Job] = {}
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._workers: List[threading.Thread] = []
        self._scheduler_thread: Optional[threading.Thread] = None
        self._running_ids: set = set()

        if self.persist_path:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_db()
            self._load()

    # ── persistence ───────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        assert self.persist_path is not None
        conn = sqlite3.connect(str(self.persist_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        if not self.persist_path:
            return
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    scheduled_at REAL,
                    updated_at REAL
                )
                """
            )
            conn.commit()

    def _save_job(self, job: Job) -> None:
        if not self.persist_path:
            return
        job.touch()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO jobs (id, payload, status, priority, scheduled_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    json.dumps(job.to_dict()),
                    job.status.value,
                    job.priority.value,
                    job.scheduled_at,
                    job.updated_at,
                ),
            )
            conn.commit()

    def _delete_job_row(self, job_id: str) -> None:
        if not self.persist_path:
            return
        with self._conn() as conn:
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            conn.commit()

    def _load(self) -> None:
        if not self.persist_path or not self.persist_path.exists():
            return
        with self._conn() as conn:
            rows = conn.execute("SELECT payload FROM jobs").fetchall()
        for row in rows:
            try:
                data = json.loads(row["payload"])
                job = Job.from_dict(data)
                # Recover interrupted runs
                if job.status == JobStatus.RUNNING:
                    job.status = JobStatus.QUEUED
                    job.progress_message = "Recovered after restart"
                self._jobs[job.id] = job
            except Exception:
                continue

    # ── queue API ─────────────────────────────────────────────────

    def enqueue(
        self,
        objective: str,
        *,
        priority: JobPriority = JobPriority.NORMAL,
        scheduled_at: Optional[float] = None,
        schedule: Optional[Dict[str, Any]] = None,
        plan_snapshot: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        max_attempts: int = 3,
    ) -> Job:
        job = Job(
            objective=objective,
            priority=priority,
            status=JobStatus.QUEUED,
            scheduled_at=scheduled_at,
            schedule=schedule,
            plan_snapshot=plan_snapshot,
            metadata=metadata or {},
            max_attempts=max_attempts,
        )
        with self._lock:
            self._jobs[job.id] = job
            self._save_job(job)
        self.events.emit(
            EventType.JOB_CREATED,
            {"job_id": job.id, "objective": objective, "priority": priority.value},
            source="job_manager",
        )
        self.events.emit(
            EventType.JOB_QUEUED,
            {"job_id": job.id, "objective": objective, "priority": priority.value},
            source="job_manager",
        )
        self._wake.set()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(
        self,
        *,
        status: Optional[JobStatus] = None,
        limit: int = 50,
    ) -> List[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        if status:
            jobs = [j for j in jobs if j.status == status]
        jobs.sort(key=lambda j: (PRIORITY_ORDER.get(j.priority, 9), j.created_at))
        return jobs[:limit]

    def queue(self) -> List[Job]:
        """Jobs waiting to run (queued/retrying and due)."""
        now = time.time()
        with self._lock:
            ready = [
                j
                for j in self._jobs.values()
                if j.status in (JobStatus.QUEUED, JobStatus.RETRYING)
                and (j.scheduled_at is None or j.scheduled_at <= now)
            ]
        ready.sort(key=lambda j: (PRIORITY_ORDER.get(j.priority, 9), j.created_at))
        return ready

    def pause(self, job_id: str) -> Job:
        with self._lock:
            job = self._require(job_id)
            if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYING):
                raise ValueError(f"Cannot pause job in status {job.status}")
            job.status = JobStatus.PAUSED
            job.touch()
            self._save_job(job)
        self.events.emit(
            EventType.JOB_PROGRESS,
            {"job_id": job_id, "progress": job.progress, "message": "paused", "paused": True},
            source="job_manager",
        )
        return job

    def resume(self, job_id: str) -> Job:
        with self._lock:
            job = self._require(job_id)
            if job.status != JobStatus.PAUSED:
                raise ValueError(f"Cannot resume job in status {job.status}")
            job.status = JobStatus.QUEUED
            job.scheduled_at = None
            job.touch()
            self._save_job(job)
        self._wake.set()
        self.events.emit(
            EventType.JOB_QUEUED,
            {"job_id": job_id, "resumed": True},
            source="job_manager",
        )
        return job

    def cancel(self, job_id: str) -> Job:
        with self._lock:
            job = self._require(job_id)
            if job.status in (JobStatus.COMPLETED, JobStatus.CANCELLED):
                return job
            job.status = JobStatus.CANCELLED
            job.completed_at = time.time()
            job.touch()
            self._save_job(job)
        self.events.emit(
            EventType.JOB_CANCELLED,
            {"job_id": job_id},
            source="job_manager",
        )
        return job

    def retry(self, job_id: str) -> Job:
        with self._lock:
            job = self._require(job_id)
            if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.COMPLETED):
                raise ValueError(f"Cannot retry job in status {job.status}")
            job.status = JobStatus.RETRYING
            job.error = None
            job.result = None
            job.progress = 0.0
            job.scheduled_at = None
            job.completed_at = None
            job.touch()
            self._save_job(job)
        self.events.emit(
            EventType.JOB_QUEUED,
            {"job_id": job_id, "retried": True, "attempt": job.attempts},
            source="job_manager",
        )
        self._wake.set()
        return job

    def set_progress(self, job_id: str, progress: float, message: str = "") -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.progress = max(0.0, min(1.0, progress))
            job.progress_message = message
            job.touch()
            self._save_job(job)
        self.events.emit(
            EventType.JOB_PROGRESS,
            {"job_id": job_id, "progress": progress, "message": message},
            source="job_manager",
        )

    def _require(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if not job:
            raise KeyError(f"Unknown job: {job_id}")
        return job

    # ── scheduling ────────────────────────────────────────────────

    def schedule(
        self,
        objective: str,
        *,
        when: Optional[float] = None,
        interval_s: Optional[float] = None,
        daily_hour: Optional[int] = None,
        weekly_weekday: Optional[int] = None,
        priority: JobPriority = JobPriority.NORMAL,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Job:
        """
        Schedule a one-shot or recurring job.
          when         – unix timestamp for one-shot
          interval_s   – repeat every N seconds
          daily_hour   – run daily at hour (0-23)
          weekly_weekday – 0=Mon … with daily_hour
        """
        if interval_s is not None:
            spec = ScheduleSpec(kind="interval", interval_s=interval_s)
            next_run = time.time() + float(interval_s)
            schedule = spec.to_dict()
            schedule["next_run"] = next_run
        elif weekly_weekday is not None:
            spec = ScheduleSpec(kind="weekly", weekday=weekly_weekday, hour=daily_hour or 9)
            next_run = spec.compute_next()
            schedule = spec.to_dict()
            schedule["next_run"] = next_run
        elif daily_hour is not None:
            spec = ScheduleSpec(kind="daily", hour=daily_hour)
            next_run = spec.compute_next()
            schedule = spec.to_dict()
            schedule["next_run"] = next_run
        else:
            next_run = when or time.time()
            schedule = ScheduleSpec(kind="once", next_run=next_run).to_dict()

        return self.enqueue(
            objective,
            priority=priority,
            scheduled_at=next_run,
            schedule=schedule,
            metadata=metadata or {},
        )

    # ── worker loop ───────────────────────────────────────────────

    def start(self) -> None:
        if self._workers:
            return
        self._stop.clear()
        for i in range(self.max_workers):
            t = threading.Thread(target=self._worker_loop, name=f"pear-job-worker-{i}", daemon=True)
            t.start()
            self._workers.append(t)
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop, name="pear-job-scheduler", daemon=True
        )
        self._scheduler_thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        for t in self._workers:
            t.join(timeout=timeout)
        self._workers.clear()
        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=timeout)
            self._scheduler_thread = None

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            job = self._claim_next()
            if job is None:
                self._wake.wait(self.poll_interval)
                self._wake.clear()
                continue
            self._execute(job)

    def _scheduler_loop(self) -> None:
        """Promote due scheduled jobs (already in QUEUED with scheduled_at)."""
        while not self._stop.is_set():
            now = time.time()
            woke = False
            with self._lock:
                for job in self._jobs.values():
                    if (
                        job.status in (JobStatus.QUEUED, JobStatus.RETRYING)
                        and job.scheduled_at is not None
                        and job.scheduled_at <= now
                    ):
                        woke = True
            if woke:
                self._wake.set()
            self._stop.wait(min(1.0, self.poll_interval * 4))

    def _claim_next(self) -> Optional[Job]:
        now = time.time()
        with self._lock:
            candidates = [
                j
                for j in self._jobs.values()
                if j.status in (JobStatus.QUEUED, JobStatus.RETRYING)
                and j.id not in self._running_ids
                and (j.scheduled_at is None or j.scheduled_at <= now)
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda j: (PRIORITY_ORDER.get(j.priority, 9), j.created_at))
            job = candidates[0]
            job.status = JobStatus.RUNNING
            job.started_at = time.time()
            job.attempts += 1
            job.touch()
            self._running_ids.add(job.id)
            self._save_job(job)
            return job

    def _execute(self, job: Job) -> None:
        self.events.emit(
            EventType.JOB_STARTED,
            {"job_id": job.id, "objective": job.objective, "attempt": job.attempts},
            source="job_manager",
        )
        _tracer_token = None
        if self.tracer is not None:
            from .tracing import set_tracer
            _tracer_token = set_tracer(self.tracer)
        try:
            self._execute_inner(job)
        finally:
            if _tracer_token is not None:
                from .tracing import reset_tracer
                reset_tracer(_tracer_token)

    def _execute_inner(self, job: Job) -> None:
        try:
            if self.runner is None:
                raise RuntimeError("No job runner configured")
            result = self.runner(job)
            with self._lock:
                # cancelled while running?
                current = self._jobs.get(job.id)
                if current and current.status == JobStatus.CANCELLED:
                    self._running_ids.discard(job.id)
                    return
                job.result = result if isinstance(result, dict) else {"ok": True, "reply": str(result)}
                job.progress = 1.0
                job.status = JobStatus.COMPLETED
                job.completed_at = time.time()
                job.touch()
                self._save_job(job)
                self._running_ids.discard(job.id)
            self.events.emit(
                EventType.JOB_COMPLETED,
                {"job_id": job.id, "ok": bool((job.result or {}).get("ok", True))},
                source="job_manager",
            )
            self._maybe_reschedule(job)
        except Exception as e:
            err = f"{e}\n{traceback.format_exc()}"
            with self._lock:
                job.error = str(e)
                job.touch()
                if job.attempts < job.max_attempts and job.status != JobStatus.CANCELLED:
                    job.status = JobStatus.RETRYING
                    job.scheduled_at = time.time() + min(30, 2 ** job.attempts)
                    self._save_job(job)
                    self._running_ids.discard(job.id)
                    try:
                        from .tracing import get_tracer
                        get_tracer().record_retry()
                    except Exception:
                        pass
                    self.events.emit(
                        EventType.JOB_FAILED,
                        {"job_id": job.id, "error": str(e), "will_retry": True},
                        source="job_manager",
                    )
                    self._wake.set()
                else:
                    job.status = JobStatus.FAILED
                    job.completed_at = time.time()
                    self._save_job(job)
                    self._running_ids.discard(job.id)
                    self.events.emit(
                        EventType.JOB_FAILED,
                        {"job_id": job.id, "error": str(e), "will_retry": False},
                        source="job_manager",
                    )

    def _maybe_reschedule(self, job: Job) -> None:
        sched = job.schedule or {}
        kind = sched.get("kind")
        if kind in (None, "once"):
            return
        spec = ScheduleSpec.from_dict(sched)
        next_run = spec.compute_next(after=time.time())
        if next_run is None:
            return
        # Enqueue a fresh job for the next occurrence
        new_sched = dict(sched)
        new_sched["next_run"] = next_run
        self.enqueue(
            job.objective,
            priority=job.priority,
            scheduled_at=next_run,
            schedule=new_sched,
            plan_snapshot=job.plan_snapshot,
            metadata={**(job.metadata or {}), "recurring_from": job.id},
            max_attempts=job.max_attempts,
        )

    # ── test helpers ──────────────────────────────────────────────

    def run_once(self, timeout: float = 5.0) -> Optional[Job]:
        """Synchronously claim and run one due job (for tests)."""
        job = self._claim_next()
        if job is None:
            # wait briefly for scheduled
            deadline = time.time() + timeout
            while time.time() < deadline and job is None:
                time.sleep(0.05)
                job = self._claim_next()
        if job is None:
            return None
        self._execute(job)
        return job

    def drain(self, timeout: float = 10.0) -> int:
        """Run until queue empty or timeout. Returns jobs executed."""
        count = 0
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.run_once(timeout=max(0.05, deadline - time.time()))
            if job is None:
                if not self.queue():
                    break
                continue
            count += 1
        return count
