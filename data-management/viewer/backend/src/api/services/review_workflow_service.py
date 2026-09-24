"""Application orchestration for immutable review and quality workflows."""

from __future__ import annotations

from datetime import UTC, datetime

from ..config import create_review_repository, create_source_workspace, get_app_config
from ..models.review_workflow import QualityRunRequest
from ..models.reviews import AnnotationRevision, EditRevision, QualityReport, ReviewDecision
from ..quality.adapters import HDF5SourceAdapter, LeRobotSourceAdapter
from ..quality.service import QualityService
from ..storage.review_base import ReviewRepository
from ..storage.source_workspace import SourceWorkspace
from .review_service import ReviewService


class ReviewRouteMismatchError(ValueError):
    """Raised when body evidence does not belong to the routed episode."""


class ReviewWorkflowService:
    """Coordinate review integrity, strict quality checks, and persistence."""

    def __init__(self, repository: ReviewRepository, *, source_workspace: SourceWorkspace | None = None) -> None:
        self.repository = repository
        self._review = ReviewService(repository)
        self._quality = QualityService()
        self._source_workspace = source_workspace

    async def create_annotation_revision(
        self,
        dataset_id: str,
        episode_index: int,
        revision: AnnotationRevision,
    ) -> AnnotationRevision:
        self._validate_route(dataset_id, episode_index, revision.source.dataset_id, revision.source.episode_index)
        await self._review.create_annotation_revision(revision)
        return revision

    async def create_edit_revision(
        self,
        dataset_id: str,
        episode_index: int,
        revision: EditRevision,
    ) -> EditRevision:
        self._validate_route(dataset_id, episode_index, revision.source.dataset_id, revision.source.episode_index)
        await self._review.create_edit_revision(revision)
        return revision

    async def create_quality_report(
        self,
        dataset_id: str,
        episode_index: int,
        report: QualityReport,
    ) -> QualityReport:
        self._validate_route(dataset_id, episode_index, report.source.dataset_id, report.source.episode_index)
        await self._review.create_quality_report(report)
        return report

    async def get_quality_report(
        self,
        dataset_id: str,
        episode_index: int,
        run_id: str,
    ) -> QualityReport | None:
        report = await self.repository.get_quality_report(run_id)
        if report is None:
            return None
        self._validate_route(dataset_id, episode_index, report.source.dataset_id, report.source.episode_index)
        return report

    async def get_latest_quality_report(self, dataset_id: str, episode_index: int) -> QualityReport | None:
        reports = await self.repository.list_quality_reports(dataset_id, episode_index)
        if not reports:
            return None
        return max(reports, key=lambda report: (report.created_at, report.run_id))

    async def run_quality(
        self,
        dataset_id: str,
        episode_index: int,
        request: QualityRunRequest,
    ) -> QualityReport:
        if self._source_workspace is None:
            raise RuntimeError("Quality source root is not configured")
        adapter = LeRobotSourceAdapter() if request.source_format == "lerobot" else HDF5SourceAdapter()
        async with self._source_workspace.open(dataset_id) as dataset_root:
            report = await self._quality.run(
                adapter=adapter,
                dataset_root=dataset_root,
                dataset_id=dataset_id,
                episode_index=episode_index,
                profile=request.profile,
                run_id=request.run_id,
                actor_id=request.actor_id,
                created_at=datetime.now(UTC),
            )
        await self._review.create_quality_report(report)
        return report

    async def create_decision(
        self,
        dataset_id: str,
        episode_index: int,
        decision: ReviewDecision,
    ) -> ReviewDecision:
        self._validate_route(dataset_id, episode_index, decision.source.dataset_id, decision.source.episode_index)
        await self._review.create_decision(decision)
        return decision

    async def get_latest_decision(self, dataset_id: str, episode_index: int) -> ReviewDecision | None:
        decisions = await self.repository.list_decisions(dataset_id, episode_index)
        if not decisions:
            return None
        return max(decisions, key=lambda decision: (decision.created_at, decision.decision_id))

    @staticmethod
    def _validate_route(
        dataset_id: str,
        episode_index: int,
        source_dataset_id: str,
        source_episode_index: int,
    ) -> None:
        if dataset_id != source_dataset_id or episode_index != source_episode_index:
            raise ReviewRouteMismatchError("Review evidence does not match the routed dataset episode")


_review_repository: ReviewRepository | None = None
_review_workflow_service: ReviewWorkflowService | None = None


def get_review_repository() -> ReviewRepository:
    """Return the configured immutable review repository singleton."""
    global _review_repository
    if _review_repository is None:
        _review_repository = create_review_repository(get_app_config())
    return _review_repository


def get_review_workflow_service() -> ReviewWorkflowService:
    """Return the configured review workflow singleton."""
    global _review_workflow_service
    if _review_workflow_service is None:
        config = get_app_config()
        _review_workflow_service = ReviewWorkflowService(
            get_review_repository(),
            source_workspace=create_source_workspace(config),
        )
    return _review_workflow_service
