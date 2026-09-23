"""Durable file-backed release jobs, transitions, and reconciliation."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, JsonValue

from ..models.releases import OperationalEvent, canonical_json_bytes
from ..models.reviews import ContractId, ImmutableContract, UtcTimestamp
from ..storage.review_base import validate_review_identifier


class JobState(StrEnum):
    """Durable release job states."""

    QUEUED = "queued"
    RUNNING = "running"
    VERIFYING = "verifying"
    PUBLISHING = "publishing"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    FAILED = "failed"
    CONFLICT = "conflict"


_TERMINAL_STATES = {JobState.SUCCEEDED, JobState.CANCELLED, JobState.FAILED, JobState.CONFLICT}
_TRANSITIONS = {
    JobState.QUEUED: {JobState.RUNNING, JobState.CANCELLED, JobState.FAILED, JobState.CONFLICT},
    JobState.RUNNING: {JobState.VERIFYING, JobState.CANCELLED, JobState.FAILED, JobState.CONFLICT},
    JobState.VERIFYING: {JobState.PUBLISHING, JobState.CANCELLED, JobState.FAILED, JobState.CONFLICT},
    JobState.PUBLISHING: {JobState.SUCCEEDED, JobState.FAILED, JobState.CONFLICT},
}


class JobConflictError(RuntimeError):
    """Raised when idempotency or release identity is reused with different inputs."""


class ReleaseJob(ImmutableContract):
    """One durable release operation."""

    job_id: ContractId
    dataset_id: ContractId
    idempotency_key: ContractId
    release_id: ContractId
    actor_id: ContractId
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    request: dict[str, JsonValue]
    state: JobState
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    cancellation_requested: bool = False
    failure_reason: str | None = Field(default=None, max_length=2000)


class ReleaseJobStore:
    """Persist release jobs and append-only transition events beneath one root."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def submit(
        self,
        *,
        idempotency_key: str,
        release_id: str,
        actor_id: str,
        request: dict[str, JsonValue],
    ) -> ReleaseJob:
        normalized = json.dumps(request, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
        request_digest = hashlib.sha256(normalized.encode()).hexdigest()
        dataset_value = request.get("dataset_id")
        if not isinstance(dataset_value, str):
            raise ValueError("Release job request requires dataset_id")
        dataset_id = validate_review_identifier(dataset_value)
        with self._locked():
            for existing in self.list_jobs():
                if existing.idempotency_key == idempotency_key:
                    if (
                        existing.release_id == release_id
                        and existing.actor_id == actor_id
                        and existing.request_digest == request_digest
                    ):
                        return existing
                    raise JobConflictError("idempotency key is already bound to different inputs")
                if existing.release_id == release_id:
                    raise JobConflictError("release ID is already bound to another job")
            now = _now()
            job = ReleaseJob(
                job_id=f"job-{uuid.uuid4().hex}",
                dataset_id=dataset_id,
                idempotency_key=idempotency_key,
                release_id=release_id,
                actor_id=actor_id,
                request_digest=request_digest,
                request=request,
                state=JobState.QUEUED,
                created_at=now,
                updated_at=now,
            )
            self._write_job(job, exclusive=True)
            self._append_event(job)
            return job

    def get(self, job_id: str) -> ReleaseJob:
        path = self.job_directory(job_id) / "job.json"
        try:
            return ReleaseJob.model_validate_json(path.read_bytes())
        except FileNotFoundError as exc:
            raise KeyError(job_id) from exc

    def list_jobs(self) -> list[ReleaseJob]:
        if not self.root.exists():
            return []
        return [ReleaseJob.model_validate_json(path.read_bytes()) for path in sorted(self.root.glob("*/*/job.json"))]

    def list_events(self, job_id: str) -> list[OperationalEvent]:
        path = self.job_directory(job_id) / "events.jsonl"
        if not path.exists():
            return []
        return [OperationalEvent.model_validate_json(line) for line in path.read_bytes().splitlines()]

    def transition(self, job_id: str, state: JobState, *, reason: str | None = None) -> ReleaseJob:
        with self._locked():
            job = self.get(job_id)
            if state not in _TRANSITIONS.get(job.state, set()):
                raise ValueError(f"Invalid release job transition: {job.state} -> {state}")
            return self._replace(job, state=state, reason=reason)

    def request_cancellation(self, job_id: str) -> ReleaseJob:
        with self._locked():
            job = self.get(job_id)
            if job.state in _TERMINAL_STATES:
                return job
            updated = job.model_copy(update={"cancellation_requested": True, "updated_at": _now()})
            self._write_job(updated)
            return updated

    def cancellation_checkpoint(self, job_id: str) -> bool:
        with self._locked():
            job = self.get(job_id)
            if not job.cancellation_requested or job.state in {JobState.PUBLISHING, *_TERMINAL_STATES}:
                return False
            self._replace(job, state=JobState.CANCELLED, reason="Cancellation requested")
            return True

    def reconcile(self, *, staging_root: Path, is_published: Any) -> list[ReleaseJob]:
        reconciled: list[ReleaseJob] = []
        with self._locked():
            for job in self.list_jobs():
                if job.state in _TERMINAL_STATES:
                    continue
                if is_published(job.dataset_id, job.release_id):
                    job = self._replace(job, state=JobState.SUCCEEDED, reason="Recovered published release")
                elif job.state is JobState.QUEUED:
                    pass
                elif (staging_root / job.dataset_id / job.job_id).is_dir():
                    job = self._replace(job, state=JobState.QUEUED, reason="Requeued after restart")
                else:
                    job = self._replace(
                        job,
                        state=JobState.FAILED,
                        reason="Staging and publication evidence are missing",
                    )
                reconciled.append(job)
        return reconciled

    def _replace(self, job: ReleaseJob, *, state: JobState, reason: str | None) -> ReleaseJob:
        updated = job.model_copy(update={"state": state, "updated_at": _now(), "failure_reason": reason})
        self._write_job(updated)
        self._append_event(updated, reason=reason)
        return updated

    def _write_job(self, job: ReleaseJob, *, exclusive: bool = False) -> None:
        directory = self.root / job.dataset_id / job.job_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "job.json"
        payload = canonical_json_bytes(job)
        if exclusive:
            with path.open("xb") as stream:
                stream.write(payload)
            return
        temporary = path.with_suffix(f".json.{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, path)

    def _append_event(self, job: ReleaseJob, *, reason: str | None = None) -> None:
        now = _now()
        event = OperationalEvent(
            operation_id=job.job_id,
            release_id=job.release_id,
            event_name=f"release.{job.state.value}",
            timestamp=now,
            observed_timestamp=now,
            actor_id=job.actor_id,
            status=job.state.value,
            attributes={"request.digest": job.request_digest},
            body={"reason": reason} if reason else {},
        )
        path = self.root / job.dataset_id / job.job_id / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as stream:
            stream.write(canonical_json_bytes(event))

    def job_directory(self, job_id: str) -> Path:
        """Resolve the unique dataset-scoped directory for a job ID."""
        validated_id = validate_review_identifier(job_id)
        matches = sorted(self.root.glob(f"*/{validated_id}"))
        if not matches:
            raise KeyError(job_id)
        if len(matches) > 1:
            raise JobConflictError(f"Job ID is not unique: {job_id}")
        return matches[0]

    @contextmanager
    def _locked(self) -> Iterator[None]:
        lock_path = self.root / ".jobs.lock"
        with lock_path.open("a+b") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _now() -> datetime:
    return datetime.now(UTC)
