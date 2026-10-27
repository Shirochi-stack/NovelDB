"""Download jobs as the queue stores them."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
INTERRUPTED = "interrupted"

ACTIVE = (QUEUED, RUNNING)
FINISHED = (DONE, FAILED, CANCELLED, INTERRUPTED)


@dataclass
class Job:
    input: str
    source: Optional[str] = None
    engine: str = "browser"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = ""
    state: str = QUEUED
    value: int = 0
    total: int = 0
    info: str = ""
    status: str = ""
    error: str = ""
    output_dir: str = ""
    outputs: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    created: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def active(self) -> bool:
        return self.state in ACTIVE

    @property
    def display_title(self) -> str:
        return self.title or self.input

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        known = {name for name in cls.__dataclass_fields__}
        job = cls(**{k: v for k, v in data.items() if k in known})
        if job.state == RUNNING:
            job.state = INTERRUPTED
            job.status = "Interrupted when the app closed"
        return job
