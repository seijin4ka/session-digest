import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    UPLOADING = "uploading"
    SPLITTING = "splitting"
    TRANSCRIBING = "transcribing"
    MERGING = "merging"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    status: JobStatus = JobStatus.UPLOADING
    progress: int = 0
    current_step: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    error: str | None = None
    results: dict[str, Any] = field(default_factory=dict)
    chunks_total: int = 0
    chunks_done: int = 0
    filename: str = ""


class JobStore:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def create_job(self, filename: str = "") -> str:
        job_id = uuid.uuid4().hex[:12]
        self._jobs[job_id] = Job(id=job_id, filename=filename)
        self._subscribers[job_id] = []
        return job_id

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def update_job(self, job_id: str, **kwargs) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return
        for key, value in kwargs.items():
            if hasattr(job, key):
                setattr(job, key, value)

    async def notify(self, job_id: str, event_data: dict) -> None:
        for queue in self._subscribers.get(job_id, []):
            await queue.put(event_data)

    def subscribe(self, job_id: str) -> asyncio.Queue:
        queue = asyncio.Queue()
        if job_id not in self._subscribers:
            self._subscribers[job_id] = []
        self._subscribers[job_id].append(queue)
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue) -> None:
        if job_id in self._subscribers:
            self._subscribers[job_id] = [
                q for q in self._subscribers[job_id] if q is not queue
            ]

    def list_jobs(self) -> list[Job]:
        return list(self._jobs.values())
