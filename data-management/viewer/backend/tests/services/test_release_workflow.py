"""Integration tests for review-gated release orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.api.models.release_workflow import ReleaseEpisodeSelection, ReleaseSubmitRequest
from src.api.models.releases import ReleaseFormat, ReleaseManifest
from src.api.models.reviews import (
    AnnotationRevision,
    EditRevision,
    QualityCheckResult,
    QualityOutcome,
    QualityReport,
    ReviewDecision,
    ReviewDecisionValue,
    SourceFileIdentity,
    SourceIdentity,
)
from src.api.release.jobs import JobConflictError, JobState, ReleaseJobStore
from src.api.release.lerobot import ReleaseReadback, ReleaseWriteResult
from src.api.release.local_publisher import LocalReleasePublisher
from src.api.release.processor import ReleaseAssembly, ReleasePackageAssembler, ReleaseProcessor
from src.api.services.release_workflow_service import ReleaseWorkflowService
from src.api.services.review_service import ReviewService
from src.api.storage.review_local import LocalReviewRepository
from src.api.storage.source_workspace import LocalSourceWorkspace


def _source_identity(
    episode_index: int,
    digest: str = "a" * 64,
    *,
    source_format: str = "lerobot",
    format_version: str = "3.0",
) -> SourceIdentity:
    return SourceIdentity(
        dataset_id="dataset-1",
        episode_index=episode_index,
        source_format=source_format,
        format_version=format_version,
        source_digest=digest,
        files=(SourceFileIdentity(relative_path="meta/info.json", size_bytes=2, sha256="b" * 64),),
    )


async def _persist_evidence(
    repository: LocalReviewRepository,
    *,
    passing: bool = True,
    episode_index: int = 0,
    suffix: str = "1",
    source_format: str = "lerobot",
    format_version: str = "3.0",
) -> ReviewDecision:
    source = _source_identity(
        episode_index,
        source_format=source_format,
        format_version=format_version,
    )
    review = ReviewService(repository)
    created_at = datetime(2025, 1, 1, tzinfo=UTC)
    annotation = AnnotationRevision(
        revision_id=f"annotation-{suffix}",
        source=source,
        actor_id="reviewer",
        created_at=created_at,
        annotation={"label": "usable"},
    )
    edit = EditRevision(
        revision_id=f"edit-{suffix}",
        source=source,
        actor_id="reviewer",
        created_at=created_at,
        operations=(),
    )
    quality = QualityReport(
        run_id=f"quality-{suffix}",
        check_set_version="1.0.0",
        source=source,
        actor_id="reviewer",
        created_at=created_at,
        episode_checks=(
            QualityCheckResult(
                check_id="source.identity",
                required=True,
                outcome=QualityOutcome.PASS if passing else QualityOutcome.FAIL,
                reason_codes=() if passing else ("source-changed",),
            ),
        ),
        package_checks=(),
    )
    decision = ReviewDecision(
        decision_id=f"decision-{suffix}",
        decision=ReviewDecisionValue.ACCEPT,
        reason_codes=("reviewed",),
        actor_id="reviewer",
        created_at=created_at,
        source=source,
        annotation_revision_id=annotation.revision_id,
        edit_revision_id=edit.revision_id,
        quality_run_id=quality.run_id,
    )
    await review.create_annotation_revision(annotation)
    await review.create_edit_revision(edit)
    await review.create_quality_report(quality)
    await review.create_decision(decision)
    return decision


@pytest.mark.parametrize(
    ("passing", "current_digest", "expected_reason"),
    [
        (False, "a" * 64, "quality-required-check-failed"),
        (True, "c" * 64, "source-identity-changed"),
    ],
)
async def test_given_ineligible_evidence_when_evaluated_then_episode_is_excluded(
    tmp_path: Path,
    passing: bool,
    current_digest: str,
    expected_reason: str,
) -> None:
    # Arrange
    repository = LocalReviewRepository(tmp_path / "release", source_roots=(tmp_path / "source",))
    decision = await _persist_evidence(repository, passing=passing)

    async def resolve_current(source: SourceIdentity) -> SourceIdentity:
        return source.model_copy(update={"source_digest": current_digest})

    service = ReleaseWorkflowService(repository, ReleaseJobStore(tmp_path / "jobs"), resolve_current)
    request = ReleaseSubmitRequest(
        release_id="release-1",
        dataset_id="dataset-1",
        actor_id="publisher",
        reason="approved training set",
        destination_kind="local",
        idempotency_key="request-1",
        target_format=ReleaseFormat(name="lerobot", version="3.0"),
        episodes=(ReleaseEpisodeSelection(episode_index=0, decision_id=decision.decision_id),),
    )

    # Act
    result = await service.evaluate(request)

    # Assert
    assert result.excluded_episodes[0].reason_codes == (expected_reason,)


async def test_given_eligible_request_when_submitted_reordered_and_cancelled_then_job_is_durable_and_idempotent(
    tmp_path: Path,
) -> None:
    # Arrange
    repository = LocalReviewRepository(tmp_path / "release", source_roots=(tmp_path / "source",))
    first_decision = await _persist_evidence(repository)
    second_decision = await _persist_evidence(repository, episode_index=1, suffix="2")

    async def resolve_current(source: SourceIdentity) -> SourceIdentity:
        return source

    job_store = ReleaseJobStore(tmp_path / "jobs")
    service = ReleaseWorkflowService(repository, job_store, resolve_current)
    request = ReleaseSubmitRequest(
        release_id="release-1",
        dataset_id="dataset-1",
        actor_id="publisher",
        reason="approved training set",
        destination_kind="local",
        idempotency_key="request-1",
        target_format=ReleaseFormat(name="lerobot", version="3.0"),
        episodes=(
            ReleaseEpisodeSelection(episode_index=0, decision_id=first_decision.decision_id),
            ReleaseEpisodeSelection(episode_index=1, decision_id=second_decision.decision_id),
        ),
    )

    # Act
    first = await service.submit(request)
    duplicate = await service.submit(request.model_copy(update={"episodes": tuple(reversed(request.episodes))}))
    cancelled = service.cancel(first.job_id)

    # Assert
    assert duplicate.job_id == first.job_id
    assert first.eligible_episodes[0].quality_run_id == "quality-1"
    assert cancelled.state is JobState.CANCELLED
    assert ReleaseJobStore(tmp_path / "jobs").get(first.job_id).state is JobState.CANCELLED
    with pytest.raises(JobConflictError):
        await service.submit(request.model_copy(update={"reason": "different reason"}))


@pytest.mark.parametrize("destination_kind", ["local", "azure"])
async def test_given_queued_release_when_processor_starts_then_verified_package_is_published(
    tmp_path: Path,
    destination_kind: str,
) -> None:
    # Arrange
    repository = LocalReviewRepository(tmp_path / "review", source_roots=(tmp_path / "source",))
    decision = await _persist_evidence(repository)

    async def resolve_current(source: SourceIdentity) -> SourceIdentity:
        return source

    async def assemble(
        request: ReleaseSubmitRequest,
        _eligible: tuple[ReleaseEpisodeSelection, ...],
        staging_root: Path,
    ) -> ReleaseAssembly:
        staging_root.mkdir(parents=True)
        (staging_root / "data.bin").write_bytes(b"release-data")
        manifest = ReleaseManifest(
            release_id=request.release_id,
            created_at=datetime(2026, 9, 23, tzinfo=UTC),
            actor_id=request.actor_id,
            source_provenance=(decision.source,),
            accepted_decision_ids=(decision.decision_id,),
            episode_index_mapping={0: 0},
            source_formats=(ReleaseFormat(name="lerobot", version="3.0"),),
            target_format=request.target_format,
            adapter_versions={"lerobot": "0.6.1"},
            tool_versions={"dataviewer": "0.1.0"},
            feature_schema={},
            episode_count=1,
            frame_count=1,
            files=(),
        )
        return ReleaseAssembly(manifest=manifest, accepted=(decision,), rejected=())

    jobs = ReleaseJobStore(tmp_path / "jobs")
    service = ReleaseWorkflowService(repository, jobs, resolve_current)
    request = ReleaseSubmitRequest(
        release_id="release-1",
        dataset_id="dataset-1",
        actor_id="publisher",
        reason="approved training set",
        destination_kind=destination_kind,
        idempotency_key="request-1",
        target_format=ReleaseFormat(name="lerobot", version="3.0"),
        episodes=(ReleaseEpisodeSelection(episode_index=0, decision_id=decision.decision_id),),
    )
    submitted = await service.submit(request)
    blob_publisher = MagicMock()
    blob_publisher.publish = AsyncMock()
    blob_publisher.list_published_releases = AsyncMock(return_value=[])
    azure_publisher_factory = AsyncMock(return_value=blob_publisher)
    processor = ReleaseProcessor(
        service,
        jobs,
        staging_root=tmp_path / "staging",
        assembler=assemble,
        local_publisher=LocalReleasePublisher(tmp_path / "releases"),
        azure_publisher_factory=azure_publisher_factory,
    )

    # Act
    await processor.start()
    await processor.stop()

    # Assert
    result = service.get_status(submitted.job_id)
    destination = tmp_path / "releases" / request.dataset_id / request.release_id
    assert result.state is JobState.SUCCEEDED
    assert result.verification.verified is True
    if destination_kind == "local":
        assert (destination / ".published.json").is_file()
        assert (destination / "metadata" / "release-manifest.json").is_file()
        assert (destination / "checksums.sha256").is_file()
        blob_publisher.publish.assert_not_awaited()
    else:
        blob_publisher.publish.assert_awaited_once_with(
            request.dataset_id,
            request.release_id,
            tmp_path / "staging" / request.dataset_id / submitted.job_id,
            owner=submitted.job_id,
        )


async def test_given_lerobot_source_when_assembled_then_worker_receives_complete_numeric_frames(tmp_path: Path) -> None:
    # Arrange
    source_root = tmp_path / "source" / "dataset-1"
    (source_root / "meta").mkdir(parents=True)
    (source_root / "data" / "chunk-000").mkdir(parents=True)
    (source_root / "meta" / "info.json").write_text(
        '{"codebase_version":"v3.0","fps":10,"data_path":"data/chunk-{episode_chunk:03d}/file-{episode_chunk:03d}.parquet","features":{"observation.state":{"dtype":"float32","shape":[2]},"action":{"dtype":"float32","shape":[2]}}}',
        encoding="utf-8",
    )
    pq.write_table(
        pa.table(
            {
                "episode_index": [0, 0],
                "frame_index": [0, 1],
                "task_index": [0, 0],
                "observation.state": [[1.0, 2.0], [3.0, 4.0]],
                "action": [[5.0, 6.0], [7.0, 8.0]],
            }
        ),
        source_root / "data" / "chunk-000" / "file-000.parquet",
    )
    repository = LocalReviewRepository(tmp_path / "review", source_roots=(tmp_path / "source",))
    decision = await _persist_evidence(repository)
    worker = MagicMock()

    def write_v3(**kwargs):
        target_root = kwargs["target_root"]
        target_root.mkdir(parents=True)
        (target_root / "dataset.bin").write_bytes(b"package")
        return ReleaseWriteResult(
            episode_index_mapping={0: 0},
            accepted_decision_ids=(decision.decision_id,),
            readback=ReleaseReadback(
                episode_count=1,
                frame_count=2,
                features=("action", "observation.state"),
                sampled_visual_frames=0,
            ),
        )

    worker.write_v3.side_effect = write_v3
    assembler = ReleasePackageAssembler(
        repository,
        LocalSourceWorkspace(tmp_path / "source"),
        worker=worker,
    )
    request = ReleaseSubmitRequest(
        release_id="release-1",
        dataset_id="dataset-1",
        actor_id="publisher",
        reason="approved training set",
        destination_kind="local",
        idempotency_key="request-1",
        target_format=ReleaseFormat(name="lerobot", version="3.0"),
        episodes=(ReleaseEpisodeSelection(episode_index=0, decision_id=decision.decision_id),),
    )

    # Act
    assembly = await assembler(request, request.episodes, tmp_path / "staging")

    # Assert
    episode = worker.write_v3.call_args.kwargs["episodes"][0]
    assert len(episode.frames) == 2
    assert episode.frames[1]["observation.state"].tolist() == [3.0, 4.0]
    assert assembly.manifest.frame_count == 2
    assert assembly.accepted == (decision,)


async def test_given_hdf5_source_when_assembled_then_worker_receives_visual_frames(tmp_path: Path) -> None:
    # Arrange
    h5py = pytest.importorskip("h5py")
    source_root = tmp_path / "source" / "dataset-1"
    source_root.mkdir(parents=True)
    with h5py.File(source_root / "episode_000000.hdf5", "w") as source:
        source.create_dataset("data/qpos", data=[(1.0, 2.0), (3.0, 4.0)])
        source.create_dataset("data/action", data=[(5.0, 6.0), (7.0, 8.0)])
        source.create_dataset(
            "observations/images/cam0",
            data=np.asarray([np.zeros((2, 2, 3)), np.ones((2, 2, 3))], dtype=np.uint8),
        )
        source.attrs["fps"] = 10
        source.attrs["task"] = "pick object"
    repository = LocalReviewRepository(tmp_path / "review", source_roots=(tmp_path / "source",))
    decision = await _persist_evidence(repository, source_format="hdf5", format_version="1.0")
    worker = MagicMock()

    def write_v3(**kwargs):
        target_root = kwargs["target_root"]
        target_root.mkdir(parents=True)
        (target_root / "dataset.bin").write_bytes(b"package")
        return ReleaseWriteResult(
            episode_index_mapping={0: 0},
            accepted_decision_ids=(decision.decision_id,),
            readback=ReleaseReadback(
                episode_count=1,
                frame_count=2,
                features=("action", "observation.images.cam0", "observation.state"),
                sampled_visual_frames=2,
            ),
        )

    worker.write_v3.side_effect = write_v3
    assembler = ReleasePackageAssembler(
        repository,
        LocalSourceWorkspace(tmp_path / "source"),
        worker=worker,
    )
    request = ReleaseSubmitRequest(
        release_id="release-1",
        dataset_id="dataset-1",
        actor_id="publisher",
        reason="approved training set",
        destination_kind="local",
        idempotency_key="request-1",
        target_format=ReleaseFormat(name="lerobot", version="3.0"),
        episodes=(ReleaseEpisodeSelection(episode_index=0, decision_id=decision.decision_id),),
    )

    # Act
    assembly = await assembler(request, request.episodes, tmp_path / "staging")

    # Assert
    episode = worker.write_v3.call_args.kwargs["episodes"][0]
    assert episode.frames[1]["observation.images.cam0"].shape == (2, 2, 3)
    assert worker.write_v3.call_args.kwargs["features"]["observation.images.cam0"]["dtype"] == "video"
    assert assembly.manifest.source_formats == (ReleaseFormat(name="hdf5", version="1.0"),)
