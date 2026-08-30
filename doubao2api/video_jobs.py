"""In-memory job store behind the OpenAI-compatible /v1/videos endpoints.

OpenAI's Videos API is asynchronous: create returns a queued job, the caller
polls until it reports ``completed``, then fetches the bytes from a separate
content endpoint. Doubao instead exposes one blocking call that takes two to
three minutes. A job here is that call moved onto a background task so its
state can be observed while it runs.

Jobs are held in memory only. A restart drops them, which is deliberate: the
video itself lives on a signed, expiring Doubao CDN URL, so a persisted job
would mostly preserve a dead link.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

JOB_LIMIT = 100

# Doubao reports no progress signal of any kind, so the percentage shown while
# a job runs is derived from elapsed time against this rough expectation. It is
# an estimate; only `status` is authoritative.
EXPECTED_SECONDS = 150.0
MAX_ESTIMATED_PROGRESS = 95

QUEUED = "queued"
IN_PROGRESS = "in_progress"
COMPLETED = "completed"
FAILED = "failed"


class VideoJob:
    """One video generation, shaped like OpenAI's ``video`` object."""

    def __init__(self, model: str, size: str, seconds: str):
        self.id = "video_" + uuid.uuid4().hex
        self.model = model
        self.size = size
        self.seconds = seconds
        self.status = QUEUED
        self.created_at = int(time.time())
        self.completed_at: Optional[int] = None
        self.error: Optional[Dict[str, str]] = None
        self.video: Dict[str, Any] = {}
        self._started_at = 0.0

    # ── State transitions ──

    def mark_running(self) -> None:
        self.status = IN_PROGRESS
        self._started_at = time.monotonic()

    def complete(self, video: Dict[str, Any]) -> None:
        """Record the finished video, correcting size/seconds to what came back.

        The request only expresses a preference; Doubao picks the actual
        resolution and trims the clip to its own frame count.
        """
        self.video = video
        self.status = COMPLETED
        self.completed_at = int(time.time())
        width, height = video.get("width"), video.get("height")
        if width and height:
            self.size = f"{width}x{height}"
        duration = video.get("duration")
        if duration:
            self.seconds = str(round(float(duration)))

    def fail(self, code: str, message: str) -> None:
        self.status = FAILED
        self.completed_at = int(time.time())
        self.error = {"code": code, "message": message}

    # ── Serialisation ──

    @property
    def progress(self) -> int:
        if self.status == COMPLETED:
            return 100
        if self.status in (QUEUED, FAILED) or not self._started_at:
            return 0
        elapsed = time.monotonic() - self._started_at
        return min(MAX_ESTIMATED_PROGRESS, int(elapsed / EXPECTED_SECONDS * 100))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "object": "video",
            "model": self.model,
            "status": self.status,
            "progress": self.progress,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "size": self.size,
            "seconds": self.seconds,
            "error": self.error,
            # Extension. The signed CDN links, so a caller that would rather
            # not stream through /content can take them directly.
            "doubao": {
                "vid": self.video.get("vid", ""),
                "video_url": self.video.get("video_url", ""),
                "cover_url": self.video.get("cover_url", ""),
            } if self.video else None,
        }


class VideoJobStore:
    """Bounded, insertion-ordered job table."""

    def __init__(self, limit: int = JOB_LIMIT):
        self.limit = limit
        self._jobs: Dict[str, VideoJob] = {}

    def create(self, model: str, size: str, seconds: str) -> VideoJob:
        job = VideoJob(model, size, seconds)
        self._jobs[job.id] = job
        self._evict()
        return job

    def get(self, job_id: str) -> Optional[VideoJob]:
        return self._jobs.get(job_id)

    def delete(self, job_id: str) -> bool:
        return self._jobs.pop(job_id, None) is not None

    def list(
        self, limit: int = 20, order: str = "desc", after: str = ""
    ) -> List[VideoJob]:
        jobs = list(self._jobs.values())
        if order != "asc":
            jobs.reverse()
        if after:
            ids = [job.id for job in jobs]
            if after in ids:
                jobs = jobs[ids.index(after) + 1:]
        return jobs[:max(0, limit)]

    def _evict(self) -> None:
        """Drop the oldest finished job once the table is over its limit.

        A running job is never evicted — its caller is presumably still polling
        it. That means the table can exceed `limit` while many jobs are in
        flight, which is bounded in practice by the rate limiter.
        """
        while len(self._jobs) > self.limit:
            for job_id, job in self._jobs.items():
                if job.status in (COMPLETED, FAILED):
                    del self._jobs[job_id]
                    break
            else:
                return
