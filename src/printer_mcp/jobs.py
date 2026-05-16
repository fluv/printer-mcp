"""In-memory job tracking.

A pod-lifetime dict of jobs keyed by the IPP job-id. Historical lookup across
restarts isn't a v1 requirement (the cost of an accidental re-print is one
sheet of paper — well under the cost of a persistent store) so there's no
SQLite, no PVC, no rehydration on startup. See discussions/890.

The ``Job`` record carries everything the resources need to render
``printer://jobs/<id>`` and ``printer://history`` — input source, pipeline
artifact paths, page-by-page outcomes, terminal state.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Job:
    """One print job's state across its lifetime in this pod."""

    job_id: int  # IPP job-id; uniquely identifies the job within the printer's power-on
    job_name: str
    submitted_at: float  # epoch seconds, time.time()
    workdir: Path  # tempdir owned by this job; deleted when the pod recycles /tmp
    source_length: int  # LaTeX source length in bytes (raw source is not kept here)
    total_pages: int
    copies: int
    pages_seen: int = 0  # last observed job-impressions-completed
    terminal_state: str | None = None  # "completed", "canceled", "aborted"
    last_error: str | None = None  # one-line summary if the job ended badly
    completed_at: float | None = None


class JobStore:
    """Pod-lifetime in-memory job table. Thread-safe."""

    def __init__(self) -> None:
        self._jobs: dict[int, Job] = {}
        self._lock = threading.Lock()

    def add(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.job_id] = job

    def get(self, job_id: int) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: int, **fields: object) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            for k, v in fields.items():
                setattr(job, k, v)
            return job

    def all(self) -> list[Job]:
        with self._lock:
            # Newest first — most-recently submitted at top of history.
            return sorted(
                self._jobs.values(), key=lambda j: j.submitted_at, reverse=True
            )


def now() -> float:
    """Indirection for tests to monkeypatch wall clock if they need to."""
    return time.time()
