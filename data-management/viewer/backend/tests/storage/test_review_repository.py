"""Behavior tests for append-only local review persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.api.models.releases import OperationalEvent
from src.api.models.reviews import (
    AnnotationRevision,
    EditRevision,
    QualityReport,
    ReviewDecision,
    ReviewDecisionValue,
    SourceFileIdentity,
    SourceIdentity,
)
from src.api.services.review_service import ReviewIntegrityError, ReviewService
from src.api.storage.review_base import DuplicateReviewRecordError, ReviewStorageError
from src.api.storage.review_local import LocalReviewRepository


@pytest.fixture
def source_identity() -> SourceIdentity:
    return SourceIdentity(
        dataset_id="sample-dataset",
        episode_index=7,
        source_format="lerobot",
        format_version="3.0",
        source_digest="a" * 64,
        files=(SourceFileIdentity(relative_path="data/episode.parquet", size_bytes=42, sha256="b" * 64),),
    )


def _annotation(source: SourceIdentity, revision_id: str, predecessor: str | None = None) -> AnnotationRevision:
    return AnnotationRevision(
        revision_id=revision_id,
        predecessor_revision_id=predecessor,
        source=source,
        actor_id="localuser",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        annotation={"rating": "success"},
    )


def _edit(source: SourceIdentity) -> EditRevision:
    return EditRevision(
        revision_id="edit-01",
        source=source,
        actor_id="localuser",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        operations=(),
    )


def _quality(source: SourceIdentity) -> QualityReport:
    return QualityReport(
        run_id="quality-01",
        check_set_version="1.0.0",
        source=source,
        actor_id="localuser",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        episode_checks=(),
        package_checks=(),
    )


class TestLocalReviewRepository:
    async def test_given_existing_id_when_created_then_collision_does_not_overwrite(
        self,
        tmp_path: Path,
        source_identity: SourceIdentity,
    ) -> None:
        # Arrange
        repository = LocalReviewRepository(tmp_path / "release-root", source_roots=(tmp_path / "datasets",))
        revision = _annotation(source_identity, "annotation-01")
        await repository.create_annotation_revision(revision)

        # Act & Assert
        assert (
            tmp_path
            / "release-root"
            / "reviews"
            / "sample-dataset"
            / "episodes"
            / "episode-000007"
            / "annotations"
            / "annotation-01.json"
        ).is_file()
        with pytest.raises(DuplicateReviewRecordError, match="annotation-01"):
            await repository.create_annotation_revision(revision)
        assert await repository.get_annotation_revision("annotation-01") == revision

    @pytest.mark.parametrize("record_id", ["../escape", "/absolute", "folder/id", "folder\\id"])
    async def test_given_unsafe_id_when_read_then_rejected(self, tmp_path: Path, record_id: str) -> None:
        # Arrange
        repository = LocalReviewRepository(tmp_path / "release-root", source_roots=(tmp_path / "datasets",))

        # Act & Assert
        with pytest.raises(ReviewStorageError, match="identifier"):
            await repository.get_annotation_revision(record_id)

    def test_given_overlapping_source_root_when_created_then_rejected(self, tmp_path: Path) -> None:
        # Arrange
        source_root = tmp_path / "datasets"

        # Act & Assert
        with pytest.raises(ReviewStorageError, match="overlap"):
            LocalReviewRepository(source_root / "releases", source_roots=(source_root,))

    def test_given_symlink_component_when_created_then_rejected(self, tmp_path: Path) -> None:
        # Arrange
        actual_root = tmp_path / "actual"
        actual_root.mkdir()
        symlink_root = tmp_path / "linked"
        symlink_root.symlink_to(actual_root, target_is_directory=True)

        # Act & Assert
        with pytest.raises(ReviewStorageError, match="symbolic link"):
            LocalReviewRepository(symlink_root / "releases", source_roots=(tmp_path / "datasets",))

    async def test_given_events_when_appended_then_each_line_is_complete_json(self, tmp_path: Path) -> None:
        # Arrange
        repository = LocalReviewRepository(tmp_path / "release-root", source_roots=(tmp_path / "datasets",))
        first = OperationalEvent(
            operation_id="operation-01",
            event_name="review.started",
            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            observed_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            actor_id="localuser",
            status="running",
        )
        second = first.model_copy(update={"event_name": "review.completed", "status": "succeeded"})

        # Act
        await repository.append_event(first)
        await repository.append_event(second)

        # Assert
        event_path = tmp_path / "release-root" / "events" / "operation-01.jsonl"
        content = event_path.read_text(encoding="utf-8")
        assert content.endswith("\n")
        assert [json.loads(line)["event_name"] for line in content.splitlines()] == [
            "review.started",
            "review.completed",
        ]

    async def test_given_quality_reports_when_listed_then_only_episode_records_are_returned(
        self,
        tmp_path: Path,
        source_identity: SourceIdentity,
    ) -> None:
        # Arrange
        repository = LocalReviewRepository(tmp_path / "release-root", source_roots=(tmp_path / "datasets",))
        report = _quality(source_identity)
        await repository.create_quality_report(report)

        # Act
        records = await repository.list_quality_reports("sample-dataset", 7)

        # Assert
        assert records == [report]

    async def test_given_decisions_when_listed_then_only_episode_records_are_returned(
        self,
        tmp_path: Path,
        source_identity: SourceIdentity,
    ) -> None:
        # Arrange
        repository = LocalReviewRepository(tmp_path / "release-root", source_roots=(tmp_path / "datasets",))
        service = ReviewService(repository)
        annotation = _annotation(source_identity, "annotation-01")
        edit = _edit(source_identity)
        quality = _quality(source_identity)
        decision = ReviewDecision(
            decision_id="decision-01",
            decision=ReviewDecisionValue.ACCEPT,
            reason_codes=("quality-approved",),
            actor_id="localuser",
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
            source=source_identity,
            annotation_revision_id=annotation.revision_id,
            edit_revision_id=edit.revision_id,
            quality_run_id=quality.run_id,
        )
        await service.create_annotation_revision(annotation)
        await service.create_edit_revision(edit)
        await service.create_quality_report(quality)
        await service.create_decision(decision)

        # Act
        records = await repository.list_decisions("sample-dataset", 7)

        # Assert
        assert records == [decision]


class TestReviewService:
    async def test_given_missing_predecessor_when_revision_created_then_rejected(
        self,
        tmp_path: Path,
        source_identity: SourceIdentity,
    ) -> None:
        # Arrange
        service = ReviewService(LocalReviewRepository(tmp_path / "release-root", source_roots=(tmp_path / "data",)))

        # Act & Assert
        with pytest.raises(ReviewIntegrityError, match="predecessor"):
            await service.create_annotation_revision(_annotation(source_identity, "annotation-02", "annotation-01"))

    async def test_given_changed_source_when_revision_appended_then_rejected(
        self,
        tmp_path: Path,
        source_identity: SourceIdentity,
    ) -> None:
        # Arrange
        service = ReviewService(LocalReviewRepository(tmp_path / "release-root", source_roots=(tmp_path / "data",)))
        await service.create_annotation_revision(_annotation(source_identity, "annotation-01"))
        changed_source = source_identity.model_copy(update={"source_digest": "c" * 64})

        # Act & Assert
        with pytest.raises(ReviewIntegrityError, match="source identity"):
            await service.create_annotation_revision(_annotation(changed_source, "annotation-02", "annotation-01"))

    async def test_given_complete_evidence_when_decision_created_then_retrievable(
        self,
        tmp_path: Path,
        source_identity: SourceIdentity,
    ) -> None:
        # Arrange
        repository = LocalReviewRepository(tmp_path / "release-root", source_roots=(tmp_path / "data",))
        service = ReviewService(repository)
        annotation = _annotation(source_identity, "annotation-01")
        edit = _edit(source_identity)
        quality = _quality(source_identity)
        decision = ReviewDecision(
            decision_id="decision-01",
            decision=ReviewDecisionValue.ACCEPT,
            reason_codes=("quality-approved",),
            actor_id="localuser",
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
            source=source_identity,
            annotation_revision_id=annotation.revision_id,
            edit_revision_id=edit.revision_id,
            quality_run_id=quality.run_id,
        )
        await service.create_annotation_revision(annotation)
        await service.create_edit_revision(edit)
        await service.create_quality_report(quality)

        # Act
        await service.create_decision(decision)

        # Assert
        assert await repository.get_decision("decision-01") == decision

    async def test_given_missing_quality_run_when_decision_created_then_rejected(
        self,
        tmp_path: Path,
        source_identity: SourceIdentity,
    ) -> None:
        # Arrange
        repository = LocalReviewRepository(tmp_path / "release-root", source_roots=(tmp_path / "data",))
        service = ReviewService(repository)
        annotation = _annotation(source_identity, "annotation-01")
        edit = _edit(source_identity)
        await service.create_annotation_revision(annotation)
        await service.create_edit_revision(edit)
        decision = ReviewDecision(
            decision_id="decision-01",
            decision=ReviewDecisionValue.REJECT,
            reason_codes=("quality-failed",),
            actor_id="localuser",
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
            source=source_identity,
            annotation_revision_id=annotation.revision_id,
            edit_revision_id=edit.revision_id,
            quality_run_id="quality-missing",
        )

        # Act & Assert
        with pytest.raises(ReviewIntegrityError, match="quality run"):
            await service.create_decision(decision)
