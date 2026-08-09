from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import uuid
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from generator.engine import GenerationCancelled, GenerationFailure, generate_level
from generator.models import GeneratedLevel, GenerationSettings


LOGGER = logging.getLogger("one_line.generator")
LOGGER.setLevel(logging.INFO)


class JobQueueFull(RuntimeError):
    pass


class ClientRateLimited(RuntimeError):
    pass


@dataclass
class GenerationJob:
    job_id: str
    settings: GenerationSettings
    client_key: str
    state: str = "queued"
    phase: str = "queued"
    percent: int = 0
    message: str = "Waiting for a generator worker"
    attempt: int = 0
    best_score: int | None = None
    target_range: list[int] = field(default_factory=list)
    uniqueness_status: str | None = None
    verifier_nodes: int | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    elapsed_seconds: float = 0.0
    cached: bool = False
    error: str | None = None
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    result: GeneratedLevel | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def public_status(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job_id": self.job_id,
            "state": self.state,
            "phase": self.phase,
            "percent": self.percent,
            "message": self.message,
            "attempt": self.attempt,
            "best_score": self.best_score,
            "target_range": self.target_range,
            "uniqueness_status": self.uniqueness_status,
            "verifier_nodes": self.verifier_nodes,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "cached": self.cached,
        }
        if self.error:
            payload["error"] = self.error
        if self.rejection_reasons:
            payload["rejection_reasons"] = self.rejection_reasons
        return payload


class JobManager:
    """Small in-process queue designed for one low-resource web worker."""

    def __init__(self) -> None:
        self.max_history = max(4, min(100, int(os.getenv("GENERATION_JOB_HISTORY", "24"))))
        self.cache_limit = max(1, min(32, int(os.getenv("GENERATION_CACHE_SIZE", "8"))))
        self.history_ttl = max(60, int(os.getenv("GENERATION_JOB_TTL_SECONDS", "1800")))
        self.queue_limit = max(1, min(20, int(os.getenv("GENERATION_QUEUE_SIZE", "4"))))
        self.worker_count = max(1, min(2, int(os.getenv("MAX_CONCURRENT_GENERATIONS", "1"))))
        self.rate_limit_count = max(1, int(os.getenv("GENERATION_RATE_LIMIT_PER_MINUTE", "6")))
        self.jobs: OrderedDict[str, GenerationJob] = OrderedDict()
        self.cache: OrderedDict[str, GeneratedLevel] = OrderedDict()
        self.client_submissions: dict[str, deque[float]] = defaultdict(deque)
        self.pending: queue.Queue[str] = queue.Queue(maxsize=self.queue_limit)
        self.lock = threading.RLock()
        self.workers: list[threading.Thread] = []
        for index in range(self.worker_count):
            worker = threading.Thread(
                target=self._worker,
                name=f"one-line-generator-{index + 1}",
                daemon=True,
            )
            worker.start()
            self.workers.append(worker)

    @staticmethod
    def _request_cache_key(settings: GenerationSettings) -> str:
        outputs = settings.outputs.as_dict()
        return settings.cache_key() + ":" + json.dumps(outputs, sort_keys=True)

    def _cleanup_locked(self) -> None:
        now = time.time()
        expired = [
            job_id
            for job_id, job in self.jobs.items()
            if job.completed_at is not None and now - job.completed_at > self.history_ttl
        ]
        for job_id in expired:
            self.jobs.pop(job_id, None)
        while len(self.jobs) > self.max_history:
            removable = next(
                (
                    job_id
                    for job_id, job in self.jobs.items()
                    if job.state in {"complete", "failed", "cancelled"}
                ),
                None,
            )
            if removable is None:
                break
            self.jobs.pop(removable, None)

    def _check_client_limit_locked(self, client_key: str) -> None:
        now = time.time()
        submissions = self.client_submissions[client_key]
        while submissions and now - submissions[0] > 60:
            submissions.popleft()
        active = sum(
            job.client_key == client_key
            and job.state not in {"complete", "failed", "cancelled"}
            for job in self.jobs.values()
        )
        if active >= 2 or len(submissions) >= self.rate_limit_count:
            raise ClientRateLimited(
                "Too many generation requests. Wait for the current job before trying again."
            )
        submissions.append(now)

    def submit(self, settings: GenerationSettings, client_key: str) -> GenerationJob:
        settings.validate()
        with self.lock:
            self._cleanup_locked()
            self._check_client_limit_locked(client_key)
            job_id = uuid.uuid4().hex
            cache_key = self._request_cache_key(settings)
            cached = self.cache.get(cache_key)
            if cached is not None and cached.validated and cached.unique:
                self.cache.move_to_end(cache_key)
                now = time.time()
                job = GenerationJob(
                    job_id=job_id,
                    settings=settings,
                    client_key=client_key,
                    state="complete",
                    phase="complete",
                    percent=100,
                    message="Loaded a previously verified deterministic result",
                    attempt=cached.diagnostics.attempts,
                    best_score=cached.difficulty.score,
                    target_range=list(settings.target_range),
                    uniqueness_status="unique",
                    created_at=now,
                    updated_at=now,
                    started_at=now,
                    completed_at=now,
                    elapsed_seconds=0.0,
                    cached=True,
                    result=cached,
                )
                self.jobs[job_id] = job
                self._log(job)
                return job

            job = GenerationJob(
                job_id=job_id,
                settings=settings,
                client_key=client_key,
                target_range=list(settings.target_range),
            )
            self.jobs[job_id] = job
            try:
                self.pending.put_nowait(job_id)
            except queue.Full as exc:
                self.jobs.pop(job_id, None)
                raise JobQueueFull(
                    "The generation queue is full. Please wait and try again."
                ) from exc
            return job

    def get(self, job_id: str) -> GenerationJob | None:
        with self.lock:
            self._cleanup_locked()
            return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> GenerationJob | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if job.state in {"complete", "failed", "cancelled"}:
                return job
            job.cancel_event.set()
            if job.state == "queued":
                job.state = "cancelled"
                job.phase = "cancelled"
                job.percent = max(job.percent, 1)
                job.message = "Generation cancelled"
                job.updated_at = job.completed_at = time.time()
                self._log(job)
            else:
                job.message = "Cancelling generation…"
                job.updated_at = time.time()
            return job

    def _progress(self, job_id: str, update: dict[str, Any]) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job.state in {"cancelled", "failed", "complete"}:
                return
            job.state = str(update.get("state", job.state))
            job.phase = str(update.get("phase", job.phase))
            job.percent = max(job.percent, int(update.get("percent", job.percent)))
            job.message = str(update.get("message", job.message))[:300]
            job.attempt = int(update.get("attempt", job.attempt))
            job.best_score = update.get("best_score", job.best_score)
            job.target_range = update.get("target_range", job.target_range)
            job.uniqueness_status = update.get(
                "uniqueness_status", job.uniqueness_status
            )
            job.verifier_nodes = update.get("verifier_nodes", job.verifier_nodes)
            job.elapsed_seconds = float(update.get("elapsed_seconds", job.elapsed_seconds))
            job.updated_at = time.time()

    def _worker(self) -> None:
        while True:
            job_id = self.pending.get()
            try:
                with self.lock:
                    job = self.jobs.get(job_id)
                    if job is None or job.state == "cancelled":
                        continue
                    job.state = "generating"
                    job.phase = "initialize"
                    job.message = "Starting deterministic generator"
                    job.started_at = time.time()
                    job.updated_at = job.started_at
                started = time.perf_counter()
                try:
                    level = generate_level(
                        job.settings,
                        progress=lambda update: self._progress(job_id, update),
                        cancel_check=job.cancel_event.is_set,
                    )
                except GenerationCancelled:
                    with self.lock:
                        job.state = "cancelled"
                        job.phase = "cancelled"
                        job.message = "Generation cancelled"
                        job.completed_at = job.updated_at = time.time()
                        job.elapsed_seconds = time.perf_counter() - started
                    self._log(job)
                    continue
                except GenerationFailure as exc:
                    with self.lock:
                        job.state = "failed"
                        job.phase = "budget_exhausted"
                        job.percent = 100
                        job.message = str(exc)
                        job.error = str(exc)
                        job.attempt = exc.attempts
                        job.best_score = exc.best_score
                        job.rejection_reasons = exc.rejection_reasons
                        job.completed_at = job.updated_at = time.time()
                        job.elapsed_seconds = time.perf_counter() - started
                    self._log(job)
                    continue
                except Exception as exc:  # keep worker alive without leaking internals
                    LOGGER.exception("generation_job_crashed", extra={"job_id": job_id})
                    with self.lock:
                        job.state = "failed"
                        job.phase = "internal_error"
                        job.percent = 100
                        job.message = "The generator stopped unexpectedly. Please retry."
                        job.error = job.message
                        job.completed_at = job.updated_at = time.time()
                        job.elapsed_seconds = time.perf_counter() - started
                    self._log(job)
                    continue

                with self.lock:
                    job.result = level
                    job.state = "complete"
                    job.phase = "complete"
                    job.percent = 100
                    job.message = "Validated unique puzzle ready"
                    job.attempt = level.diagnostics.attempts
                    job.best_score = level.difficulty.score
                    job.uniqueness_status = "unique"
                    job.elapsed_seconds = time.perf_counter() - started
                    job.completed_at = job.updated_at = time.time()
                    cache_key = self._request_cache_key(job.settings)
                    self.cache[cache_key] = level
                    self.cache.move_to_end(cache_key)
                    while len(self.cache) > self.cache_limit:
                        self.cache.popitem(last=False)
                self._log(job)
            finally:
                self.pending.task_done()

    @staticmethod
    def _log(job: GenerationJob) -> None:
        level = job.result
        payload = {
            "event": "generation_finished",
            "job_id": job.job_id,
            "dimensions": f"{job.settings.width}x{job.settings.height}",
            "difficulty": job.settings.difficulty.value,
            "mode": job.settings.shape_mode.value,
            "seed": job.settings.seed,
            "state": job.state,
            "generation_time": round(job.elapsed_seconds, 4),
            "candidate_attempts": job.attempt,
            "difficulty_score": level.difficulty.score if level else job.best_score,
            "uniqueness_check_time": (
                round(level.diagnostics.uniqueness_check_seconds, 4) if level else None
            ),
        }
        LOGGER.info(json.dumps(payload, sort_keys=True))


JOB_MANAGER = JobManager()
