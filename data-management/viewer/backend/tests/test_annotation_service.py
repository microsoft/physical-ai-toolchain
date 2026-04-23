"""Unit tests for AnnotationService CRUD and analysis logic."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.api.models.annotations import (
    AnomalyAnnotation,
    ConfidenceLevel,
    DataQualityAnnotation,
    DataQualityLevel,
    EpisodeAnnotation,
    QualityScore,
    TaskCompletenessAnnotation,
    TaskCompletenessRating,
    TrajectoryFlag,
    TrajectoryQualityAnnotation,
    TrajectoryQualityMetrics,
)
from src.api.models.datasources import EpisodeData, EpisodeMeta, TrajectoryPoint
from src.api.services.annotation_service import AnnotationService
from src.api.storage import LocalStorageAdapter


def _build_annotation(annotator_id: str = "alice", rating: QualityScore = QualityScore.FOUR) -> EpisodeAnnotation:
    return EpisodeAnnotation(
        annotator_id=annotator_id,
        timestamp=datetime.now(UTC),
        task_completeness=TaskCompletenessAnnotation(
            rating=TaskCompletenessRating.SUCCESS,
            confidence=ConfidenceLevel.FOUR,
        ),
        trajectory_quality=TrajectoryQualityAnnotation(
            overall_score=rating,
            metrics=TrajectoryQualityMetrics(
                smoothness=QualityScore.FOUR,
                efficiency=QualityScore.FOUR,
                safety=QualityScore.FOUR,
                precision=QualityScore.FOUR,
            ),
        ),
        data_quality=DataQualityAnnotation(overall_quality=DataQualityLevel.GOOD),
        anomalies=AnomalyAnnotation(),
    )


def _make_episode(points: list[TrajectoryPoint]) -> EpisodeData:
    return EpisodeData(
        meta=EpisodeMeta(index=0, length=len(points), task_index=0),
        trajectory_data=points,
    )


def _trajectory_point(frame: int, positions: list[float], velocities: list[float]) -> TrajectoryPoint:
    return TrajectoryPoint(
        timestamp=float(frame) * 0.1,
        frame=frame,
        joint_positions=positions,
        joint_velocities=velocities,
        end_effector_pose=[0.0] * 6,
        gripper_state=0.0,
    )


@pytest.fixture
def service(tmp_path) -> AnnotationService:
    return AnnotationService(storage_adapter=LocalStorageAdapter(str(tmp_path)))


class TestAnnotationServiceConstruction:
    @pytest.mark.asyncio
    async def test_uses_provided_adapter(self, tmp_path):
        """Provided adapter is used for persistence (verified via round-trip)."""
        adapter = LocalStorageAdapter(str(tmp_path))
        svc = AnnotationService(storage_adapter=adapter)
        await svc.save_annotation("ds", 0, _build_annotation())
        # The adapter passed in must contain the saved data.
        loaded = await adapter.get_annotation("ds", 0)
        assert loaded is not None
        assert loaded.annotations[0].annotator_id == "alice"

    @pytest.mark.asyncio
    async def test_falls_back_to_local_adapter(self, tmp_path):
        """When only ``base_path`` is provided the service persists to the local filesystem."""
        svc = AnnotationService(base_path=str(tmp_path))
        await svc.save_annotation("ds", 0, _build_annotation())
        # A fresh LocalStorageAdapter pointed at the same path can read the persisted file.
        loaded = await LocalStorageAdapter(str(tmp_path)).get_annotation("ds", 0)
        assert loaded is not None
        assert loaded.annotations[0].annotator_id == "alice"


class TestSaveAndGet:
    @pytest.mark.asyncio
    async def test_save_creates_new_file(self, service: AnnotationService):
        ann = _build_annotation()
        result = await service.save_annotation("ds", 0, ann)
        assert len(result.annotations) == 1
        fetched = await service.get_annotation("ds", 0)
        assert fetched is not None
        assert fetched.annotations[0].annotator_id == "alice"

    @pytest.mark.asyncio
    async def test_save_updates_existing_annotator(self, service: AnnotationService):
        await service.save_annotation("ds", 0, _build_annotation("alice", QualityScore.TWO))
        updated = await service.save_annotation("ds", 0, _build_annotation("alice", QualityScore.FIVE))
        assert len(updated.annotations) == 1
        assert updated.annotations[0].trajectory_quality.overall_score == QualityScore.FIVE.value

    @pytest.mark.asyncio
    async def test_save_appends_new_annotator(self, service: AnnotationService):
        await service.save_annotation("ds", 0, _build_annotation("alice"))
        result = await service.save_annotation("ds", 0, _build_annotation("bob"))
        assert {a.annotator_id for a in result.annotations} == {"alice", "bob"}

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, service: AnnotationService):
        assert await service.get_annotation("ds", 99) is None


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_all_annotators(self, service: AnnotationService):
        await service.save_annotation("ds", 0, _build_annotation("alice"))
        assert await service.delete_annotation("ds", 0) is True
        assert await service.get_annotation("ds", 0) is None

    @pytest.mark.asyncio
    async def test_delete_unknown_returns_false(self, service: AnnotationService):
        assert await service.delete_annotation("ds", 0) is False

    @pytest.mark.asyncio
    async def test_delete_specific_annotator(self, service: AnnotationService):
        await service.save_annotation("ds", 0, _build_annotation("alice"))
        await service.save_annotation("ds", 0, _build_annotation("bob"))
        assert await service.delete_annotation("ds", 0, annotator_id="alice") is True
        remaining = await service.get_annotation("ds", 0)
        assert remaining is not None
        assert [a.annotator_id for a in remaining.annotations] == ["bob"]

    @pytest.mark.asyncio
    async def test_delete_specific_annotator_missing_file(self, service: AnnotationService):
        assert await service.delete_annotation("ds", 0, annotator_id="alice") is False

    @pytest.mark.asyncio
    async def test_delete_specific_annotator_not_found(self, service: AnnotationService):
        await service.save_annotation("ds", 0, _build_annotation("alice"))
        assert await service.delete_annotation("ds", 0, annotator_id="bob") is False

    @pytest.mark.asyncio
    async def test_delete_last_annotator_removes_file(self, service: AnnotationService):
        await service.save_annotation("ds", 0, _build_annotation("alice"))
        assert await service.delete_annotation("ds", 0, annotator_id="alice") is True
        assert await service.get_annotation("ds", 0) is None


class TestRunAutoAnalysis:
    @pytest.mark.asyncio
    async def test_short_trajectory_returns_neutral(self, service: AnnotationService):
        ep = _make_episode([_trajectory_point(0, [0.0] * 6, [0.0] * 6)])
        result = await service.run_auto_analysis("ds", 0, ep)
        assert result.suggested_rating == 3
        assert result.confidence == 0.0
        assert result.flags == []

    @pytest.mark.asyncio
    async def test_smooth_trajectory_no_flags(self, service: AnnotationService):
        points = [_trajectory_point(i, [float(i)] * 6, [0.5] * 6) for i in range(10)]
        result = await service.run_auto_analysis("ds", 0, _make_episode(points))
        assert TrajectoryFlag.JITTERY not in result.flags
        assert TrajectoryFlag.HESITATION not in result.flags
        assert result.suggested_rating >= 1

    @pytest.mark.asyncio
    async def test_jittery_trajectory_flagged(self, service: AnnotationService):
        points = []
        for i in range(20):
            vel = 5.0 if i % 2 == 0 else 0.0
            points.append(_trajectory_point(i, [float(i)] * 6, [vel] * 6))
        result = await service.run_auto_analysis("ds", 0, _make_episode(points))
        assert TrajectoryFlag.JITTERY in result.flags

    @pytest.mark.asyncio
    async def test_hesitation_flagged(self, service: AnnotationService):
        # 3 streaks of >10 low-velocity frames separated by activity
        points: list[TrajectoryPoint] = []
        frame = 0
        for _ in range(3):
            for _ in range(15):
                points.append(_trajectory_point(frame, [0.0] * 6, [0.0] * 6))
                frame += 1
            points.append(_trajectory_point(frame, [0.0] * 6, [1.0] * 6))
            frame += 1
        result = await service.run_auto_analysis("ds", 0, _make_episode(points))
        assert TrajectoryFlag.HESITATION in result.flags

    @pytest.mark.asyncio
    async def test_correction_heavy_flagged(self, service: AnnotationService):
        # Alternate position direction each frame across all joints
        points = []
        for i in range(20):
            pos = [float(i % 2)] * 6
            points.append(_trajectory_point(i, pos, [0.0] * 6))
        result = await service.run_auto_analysis("ds", 0, _make_episode(points))
        assert TrajectoryFlag.CORRECTION_HEAVY in result.flags


class TestGetSummary:
    @pytest.mark.asyncio
    async def test_empty_dataset(self, service: AnnotationService):
        summary = await service.get_summary("ds", total_episodes=10)
        assert summary.annotated_episodes == 0
        assert summary.task_completeness_distribution == {}

    @pytest.mark.asyncio
    async def test_aggregates_distributions(self, service: AnnotationService):
        await service.save_annotation("ds", 0, _build_annotation("alice", QualityScore.FIVE))
        await service.save_annotation("ds", 1, _build_annotation("bob", QualityScore.FIVE))
        await service.save_annotation("ds", 2, _build_annotation("carol", QualityScore.THREE))
        summary = await service.get_summary("ds", total_episodes=10)
        assert summary.annotated_episodes == 3
        assert summary.quality_score_distribution[5] == 2
        assert summary.quality_score_distribution[3] == 1
        assert summary.task_completeness_distribution["success"] == 3
