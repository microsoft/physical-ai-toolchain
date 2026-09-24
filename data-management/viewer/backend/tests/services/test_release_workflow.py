"""Integration tests for review-gated release orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.api.models.release_workflow import (
    EligibilityCandidate,
    EligibilitySnapshot,
    ReleaseEpisodeSelection,
    ReleaseJobRequest,
    ReleaseSubmitRequest,
)
from src.api.models.releases import PackageQualityReport, QualityEvidenceReference, ReleaseFormat, ReleaseManifest
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
from src.api.services.release_workflow_service import NoEligibleEpisodesError, ReleaseWorkflowService
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


def _quality_reference(decision: ReviewDecision, release_episode_index: int = 0) -> QualityEvidenceReference:
    return QualityEvidenceReference(
        source_episode_index=decision.source.episode_index,
        release_episode_index=release_episode_index,
        decision_id=decision.decision_id,
        quality_run_id=decision.quality_run_id,
        quality_report_path=f"metadata/quality/{decision.quality_run_id}.json",
        check_set_version="1.0.0",
        required_outcome=QualityOutcome.PASS,
    )


def _package_quality(
    target_format: ReleaseFormat,
    *,
    frame_count: int,
    features: tuple[str, ...],
) -> PackageQualityReport:
    return PackageQualityReport(
        target_format=target_format,
        episode_count=1,
        frame_count=frame_count,
        episode_frame_counts={0: frame_count},
        features=features,
        nonvisual_rows_read_back=frame_count,
        visual_samples=(),
        inventory_verified=True,
        checksums_verified=True,
    )


async def _persist_evidence(
    repository: LocalReviewRepository,
    *,
    passing: bool = True,
    episode_index: int = 0,
    suffix: str = "1",
    source_format: str = "lerobot",
    format_version: str = "3.0",
    decision_value: ReviewDecisionValue = ReviewDecisionValue.ACCEPT,
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
        decision=decision_value,
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


async def test_given_mixed_dataset_candidates_when_submitted_then_job_persists_accepted_snapshot_only(
    tmp_path: Path,
) -> None:
    # Arrange
    repository = LocalReviewRepository(tmp_path / "release", source_roots=(tmp_path / "source",))
    accepted = await _persist_evidence(repository, episode_index=0, suffix="accepted")
    rejected = await _persist_evidence(
        repository,
        episode_index=1,
        suffix="rejected",
        decision_value=ReviewDecisionValue.REJECT,
    )

    async def resolve_current(source: SourceIdentity) -> SourceIdentity:
        return source

    async def resolve_candidates(_dataset_id: str) -> tuple[int, ...]:
        return (0, 1, 2)

    jobs = ReleaseJobStore(tmp_path / "jobs")
    service = ReleaseWorkflowService(repository, jobs, resolve_current, resolve_candidates)
    request = ReleaseSubmitRequest(
        release_id="release-1",
        dataset_id="dataset-1",
        actor_id="publisher",
        reason="approved training set",
        destination_kind="local",
        idempotency_key="request-1",
        target_format=ReleaseFormat(name="lerobot", version="3.0"),
        episodes=(),
    )

    # Act
    response = await service.submit(request)
    persisted = ReleaseJobRequest.model_validate(jobs.get(response.job_id).request)

    # Assert
    assert persisted.episodes == (ReleaseEpisodeSelection(episode_index=0, decision_id=accepted.decision_id),)
    assert persisted.eligibility_snapshot.eligibility_fingerprint == response.eligibility_fingerprint
    assert [candidate.disposition for candidate in persisted.eligibility_snapshot.candidates] == [
        "accepted",
        "rejected",
        "excluded",
    ]
    assert persisted.eligibility_snapshot.candidates[1].decision_id == rejected.decision_id
    assert persisted.eligibility_snapshot.candidates[2].reason_codes == ("unreviewed",)

    await _persist_evidence(repository, episode_index=2, suffix="new-review")
    with pytest.raises(JobConflictError):
        await service.submit(request)


@pytest.mark.parametrize(
    ("passing", "current_digest", "expected_reason"),
    [
        (False, "a" * 64, "failed-quality"),
        (True, "c" * 64, "stale-source"),
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
    with pytest.raises(NoEligibleEpisodesError):
        await service.submit(request)


async def test_given_duplicate_dataset_candidates_when_evaluated_then_inventory_is_rejected(tmp_path: Path) -> None:
    # Arrange
    repository = LocalReviewRepository(tmp_path / "release", source_roots=(tmp_path / "source",))

    async def resolve_current(source: SourceIdentity) -> SourceIdentity:
        return source

    async def resolve_candidates(_dataset_id: str) -> tuple[int, ...]:
        return (0, 0)

    service = ReleaseWorkflowService(
        repository,
        ReleaseJobStore(tmp_path / "jobs"),
        resolve_current,
        resolve_candidates,
    )
    request = ReleaseSubmitRequest(
        release_id="release-1",
        dataset_id="dataset-1",
        actor_id="publisher",
        reason="approved training set",
        destination_kind="local",
        idempotency_key="request-1",
        target_format=ReleaseFormat(name="lerobot", version="3.0"),
        episodes=(ReleaseEpisodeSelection(episode_index=0, decision_id="decision-1"),),
    )

    # Act and assert
    with pytest.raises(ValueError, match="duplicate episodes"):
        await service.evaluate(request)


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
        quality_report = await repository.get_quality_report(decision.quality_run_id)
        assert quality_report is not None
        manifest = ReleaseManifest(
            release_id=request.release_id,
            created_at=datetime(2026, 9, 23, tzinfo=UTC),
            actor_id=request.actor_id,
            source_provenance=(decision.source,),
            accepted_decision_ids=(decision.decision_id,),
            rejected_decision_ids=(),
            excluded_episode_indices=(),
            episode_index_mapping={0: 0},
            source_formats=(ReleaseFormat(name="lerobot", version="3.0"),),
            target_format=request.target_format,
            adapter_versions={"lerobot": "0.6.1"},
            tool_versions={"dataviewer": "0.1.0"},
            feature_schema={},
            candidate_count=1,
            accepted_count=1,
            rejected_count=0,
            excluded_count=0,
            nonincluded_count=0,
            episode_count=1,
            frame_count=1,
            quality_evidence=(_quality_reference(decision),),
            files=(),
        )
        return ReleaseAssembly(
            manifest=manifest,
            accepted=(decision,),
            rejected=(),
            quality_reports=(quality_report,),
            package_quality=_package_quality(request.target_format, frame_count=1, features=()),
        )

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
    rejected = await _persist_evidence(
        repository,
        episode_index=1,
        suffix="rejected",
        decision_value=ReviewDecisionValue.REJECT,
    )
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
                episode_frame_counts={0: 2},
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
    request = ReleaseJobRequest(
        release_id="release-1",
        dataset_id="dataset-1",
        actor_id="publisher",
        reason="approved training set",
        destination_kind="local",
        idempotency_key="request-1",
        target_format=ReleaseFormat(name="lerobot", version="3.0"),
        episodes=(ReleaseEpisodeSelection(episode_index=0, decision_id=decision.decision_id),),
        eligibility_snapshot=EligibilitySnapshot(
            dataset_id="dataset-1",
            candidates=(
                EligibilityCandidate(
                    episode_index=0,
                    disposition="accepted",
                    reason_codes=(),
                    decision_id=decision.decision_id,
                    quality_run_id=decision.quality_run_id,
                    source=decision.source,
                ),
                EligibilityCandidate(
                    episode_index=1,
                    disposition="rejected",
                    reason_codes=("rejected",),
                    review_reason_codes=rejected.reason_codes,
                    decision_id=rejected.decision_id,
                    quality_run_id=rejected.quality_run_id,
                    source=rejected.source,
                ),
                EligibilityCandidate(
                    episode_index=2,
                    disposition="excluded",
                    reason_codes=("unreviewed",),
                ),
            ),
            eligibility_fingerprint="f" * 64,
        ),
    )

    # Act
    assembly = await assembler(request, request.episodes, tmp_path / "staging")

    # Assert
    episode = worker.write_v3.call_args.kwargs["episodes"][0]
    assert len(episode.frames) == 2
    assert episode.frames[1]["observation.state"].tolist() == [3.0, 4.0]
    assert assembly.manifest.frame_count == 2
    assert assembly.accepted == (decision,)
    assert assembly.rejected == (rejected,)
    assert tuple(candidate.episode_index for candidate in assembly.excluded) == (2,)
    assert assembly.manifest.candidate_count == 3
    assert assembly.manifest.nonincluded_count == 2
    assert tuple(report.run_id for report in assembly.quality_reports) == (decision.quality_run_id,)
    assert assembly.manifest.quality_evidence == (_quality_reference(decision),)
    assert assembly.package_quality.nonvisual_rows_read_back == 2


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
                episode_frame_counts={0: 2},
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
    request = ReleaseJobRequest(
        release_id="release-1",
        dataset_id="dataset-1",
        actor_id="publisher",
        reason="approved training set",
        destination_kind="local",
        idempotency_key="request-1",
        target_format=ReleaseFormat(name="lerobot", version="3.0"),
        episodes=(ReleaseEpisodeSelection(episode_index=0, decision_id=decision.decision_id),),
        eligibility_snapshot=EligibilitySnapshot(
            dataset_id="dataset-1",
            candidates=(
                EligibilityCandidate(
                    episode_index=0,
                    disposition="accepted",
                    reason_codes=(),
                    decision_id=decision.decision_id,
                    quality_run_id=decision.quality_run_id,
                    source=decision.source,
                ),
            ),
            eligibility_fingerprint="f" * 64,
        ),
    )

    # Act
    assembly = await assembler(request, request.episodes, tmp_path / "staging")

    # Assert
    episode = worker.write_v3.call_args.kwargs["episodes"][0]
    assert episode.frames[1]["observation.images.cam0"].shape == (2, 2, 3)
    assert worker.write_v3.call_args.kwargs["features"]["observation.images.cam0"]["dtype"] == "video"
    assert assembly.manifest.source_formats == (ReleaseFormat(name="hdf5", version="1.0"),)
    assert tuple(report.run_id for report in assembly.quality_reports) == (decision.quality_run_id,)
