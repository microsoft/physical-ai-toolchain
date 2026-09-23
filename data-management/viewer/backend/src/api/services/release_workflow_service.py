"""Review-gated durable release workflow orchestration."""

from __future__ import annotations

import os
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from ..config import create_source_workspace, get_app_config
from ..models.release_workflow import (
    EligibleEpisode,
    ExcludedEpisode,
    ReleaseEligibility,
    ReleaseProgress,
    ReleaseSubmitRequest,
    ReleaseVerification,
    ReleaseWorkflowResponse,
)
from ..models.releases import canonical_json_bytes
from ..models.reviews import ReviewDecisionValue, SourceIdentity
from ..quality.service import required_checks_pass
from ..release.jobs import JobState, ReleaseJobStore
from ..storage.review_base import ReviewRepository
from ..storage.source_workspace import resolve_source_identity
from .review_workflow_service import get_review_repository

CurrentSourceResolver = Callable[[SourceIdentity], Awaitable[SourceIdentity]]
JobNotifier = Callable[[str], None]


class NoEligibleEpisodesError(ValueError):
    """Raised when a release request contains no releasable episodes."""


class ReleaseWorkflowService:
    """Compute eligibility and coordinate durable release job state."""

    def __init__(
        self,
        repository: ReviewRepository,
        job_store: ReleaseJobStore,
        current_source_resolver: CurrentSourceResolver,
        job_notifier: JobNotifier | None = None,
    ) -> None:
        self._repository = repository
        self._jobs = job_store
        self._resolve_current_source = current_source_resolver
        self._job_notifier = job_notifier

    @property
    def repository(self) -> ReviewRepository:
        """Return the immutable review repository used by release processing."""
        return self._repository

    @property
    def job_store(self) -> ReleaseJobStore:
        """Return the durable job store used by release processing."""
        return self._jobs

    def set_job_notifier(self, notifier: JobNotifier) -> None:
        """Register the in-process processor notification boundary."""
        self._job_notifier = notifier

    async def evaluate(self, request: ReleaseSubmitRequest) -> ReleaseEligibility:
        eligible: list[EligibleEpisode] = []
        excluded: list[ExcludedEpisode] = []
        for selection in sorted(request.episodes, key=lambda item: (item.episode_index, item.decision_id)):
            reasons: list[str] = []
            decision = await self._repository.get_decision(selection.decision_id)
            if decision is None:
                reasons.append("decision-not-found")
            elif (
                decision.source.dataset_id != request.dataset_id
                or decision.source.episode_index != selection.episode_index
            ):
                reasons.append("decision-source-mismatch")
            elif decision.decision is not ReviewDecisionValue.ACCEPT:
                reasons.append("decision-not-accepted")
            else:
                annotation = await self._repository.get_annotation_revision(decision.annotation_revision_id)
                edit = await self._repository.get_edit_revision(decision.edit_revision_id)
                quality = await self._repository.get_quality_report(decision.quality_run_id)
                if annotation is None or edit is None:
                    reasons.append("review-revision-missing")
                if quality is None:
                    reasons.append("quality-report-missing")
                elif quality.source != decision.source:
                    reasons.append("quality-source-mismatch")
                elif not required_checks_pass(quality):
                    reasons.append("quality-required-check-failed")
                current_source = await self._resolve_current_source(decision.source)
                if current_source != decision.source:
                    reasons.append("source-identity-changed")
                if not reasons:
                    eligible.append(
                        EligibleEpisode(
                            episode_index=selection.episode_index,
                            decision_id=decision.decision_id,
                            quality_run_id=decision.quality_run_id,
                        )
                    )
            if reasons:
                excluded.append(
                    ExcludedEpisode(
                        episode_index=selection.episode_index,
                        reason_codes=tuple(dict.fromkeys(reasons)),
                    )
                )
        return ReleaseEligibility(eligible_episodes=tuple(eligible), excluded_episodes=tuple(excluded))

    async def submit(self, request: ReleaseSubmitRequest) -> ReleaseWorkflowResponse:
        eligibility = await self.evaluate(request)
        if not eligibility.eligible_episodes:
            raise NoEligibleEpisodesError("Release request has no eligible episodes")
        normalized_request = request.model_dump(mode="json", by_alias=False)
        normalized_request["episodes"] = sorted(
            normalized_request["episodes"],
            key=lambda item: (item["episode_index"], item["decision_id"]),
        )
        job = self._jobs.submit(
            idempotency_key=request.idempotency_key,
            release_id=request.release_id,
            actor_id=request.actor_id,
            request=normalized_request,
        )
        existing = self._read_record(job.job_id)
        if existing is not None:
            return self._with_job(existing, job)
        response = ReleaseWorkflowResponse(
            release_id=job.release_id,
            job_id=job.job_id,
            state=job.state,
            eligible_episodes=eligibility.eligible_episodes,
            excluded_episodes=eligibility.excluded_episodes,
        )
        self._write_record(response, exclusive=True)
        if self._job_notifier is not None:
            self._job_notifier(job.job_id)
        return response

    def get_status(self, job_id: str) -> ReleaseWorkflowResponse:
        job = self._jobs.get(job_id)
        record = self._require_record(job_id)
        return self._with_job(record, job)

    def list_statuses(self, dataset_id: str) -> list[ReleaseWorkflowResponse]:
        """Return newest-first release jobs for one source dataset."""
        jobs = sorted(
            (job for job in self._jobs.list_jobs() if job.dataset_id == dataset_id),
            key=lambda job: job.updated_at,
            reverse=True,
        )
        return [self._with_job(self._require_record(job.job_id), job) for job in jobs]

    def inspect(self, release_id: str) -> ReleaseWorkflowResponse:
        job = next((candidate for candidate in self._jobs.list_jobs() if candidate.release_id == release_id), None)
        if job is None:
            raise KeyError(release_id)
        return self._with_job(self._require_record(job.job_id), job)

    def cancel(self, job_id: str) -> ReleaseWorkflowResponse:
        self._jobs.request_cancellation(job_id)
        self._jobs.cancellation_checkpoint(job_id)
        return self.get_status(job_id)

    def transition(self, job_id: str, state: JobState) -> ReleaseWorkflowResponse:
        """Advance a job and keep its workflow response synchronized."""
        job = self._jobs.transition(job_id, state)
        record = self._require_record(job_id).model_copy(update={"state": job.state})
        self._write_record(record)
        return record

    def complete_success(self, job_id: str, *, manifest_path: str, checksums_path: str) -> ReleaseWorkflowResponse:
        job = self._jobs.get(job_id)
        progression = (JobState.QUEUED, JobState.RUNNING, JobState.VERIFYING, JobState.PUBLISHING, JobState.SUCCEEDED)
        current_index = progression.index(job.state)
        for state in progression[current_index + 1 :]:
            job = self._jobs.transition(job_id, state)
        record = self._require_record(job_id).model_copy(
            update={
                "state": JobState.SUCCEEDED,
                "verification": ReleaseVerification(
                    manifest_path=manifest_path,
                    checksums_path=checksums_path,
                    verified=True,
                ),
            }
        )
        self._write_record(record)
        return record

    def complete_failure(
        self,
        job_id: str,
        reason: str,
        *,
        conflict: bool = False,
    ) -> ReleaseWorkflowResponse:
        """Persist a failed or conflicting terminal result."""
        state = JobState.CONFLICT if conflict else JobState.FAILED
        job = self._jobs.get(job_id)
        if job.state not in {JobState.CANCELLED, JobState.FAILED, JobState.CONFLICT, JobState.SUCCEEDED}:
            job = self._jobs.transition(job_id, state, reason=reason)
        record = self._require_record(job_id).model_copy(
            update={"state": job.state, "conflict": reason if conflict else None}
        )
        self._write_record(record)
        return record

    def _require_record(self, job_id: str) -> ReleaseWorkflowResponse:
        record = self._read_record(job_id)
        if record is None:
            raise KeyError(job_id)
        return record

    def _read_record(self, job_id: str) -> ReleaseWorkflowResponse | None:
        try:
            path = self._jobs.job_directory(job_id) / "workflow.json"
            return ReleaseWorkflowResponse.model_validate_json(path.read_bytes())
        except (FileNotFoundError, KeyError):
            return None

    def _write_record(self, record: ReleaseWorkflowResponse, *, exclusive: bool = False) -> None:
        path = self._jobs.job_directory(record.job_id) / "workflow.json"
        payload = canonical_json_bytes(record)
        if exclusive:
            try:
                with path.open("xb") as stream:
                    stream.write(payload)
            except FileExistsError:
                return
            return
        temporary = path.with_suffix(f".json.{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, path)

    @staticmethod
    def _with_job(record: ReleaseWorkflowResponse, job) -> ReleaseWorkflowResponse:
        progress_by_state = {
            JobState.QUEUED: (0, "Waiting to start"),
            JobState.RUNNING: (10, "Assembling release package"),
            JobState.VERIFYING: (75, "Verifying package integrity"),
            JobState.PUBLISHING: (90, "Publishing verified package"),
            JobState.SUCCEEDED: (100, "Release complete"),
            JobState.CANCELLED: (None, "Release cancelled"),
            JobState.FAILED: (None, "Release failed"),
            JobState.CONFLICT: (None, "Release conflicted with an existing destination"),
        }
        percent, message = progress_by_state[job.state]
        return record.model_copy(
            update={
                "state": job.state,
                "progress": ReleaseProgress(percent=percent, message=message, updated_at=job.updated_at),
            }
        )


_release_workflow_service: ReleaseWorkflowService | None = None


def get_release_workflow_service() -> ReleaseWorkflowService:
    """Return the configured durable release workflow singleton."""
    global _release_workflow_service
    if _release_workflow_service is None:
        config = get_app_config()
        root = Path(config.dataviewer_release_root)
        source_workspace = create_source_workspace(config)
        _release_workflow_service = ReleaseWorkflowService(
            get_review_repository(),
            ReleaseJobStore(root / "release-jobs"),
            lambda source: resolve_source_identity(source_workspace, source),
        )
    return _release_workflow_service
