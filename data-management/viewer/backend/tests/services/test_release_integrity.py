"""Behavior tests for canonical release artifact finalization."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.api.models.release_workflow import EligibilityCandidate
from src.api.models.releases import (
    PackageQualityReport,
    QualityEvidenceReference,
    ReleaseFormat,
    ReleaseManifest,
)
from src.api.models.reviews import (
    QualityCheckResult,
    QualityOutcome,
    QualityReport,
    ReviewDecision,
    ReviewDecisionValue,
    SourceFileIdentity,
    SourceIdentity,
)
from src.api.release.integrity import finalize_release, verify_release


def _decision(value: ReviewDecisionValue, decision_id: str, *, episode_index: int = 0) -> ReviewDecision:
    digest = hashlib.sha256(decision_id.encode()).hexdigest()
    source = SourceIdentity(
        dataset_id="source-dataset",
        episode_index=episode_index,
        source_format="lerobot",
        format_version="3.0",
        source_digest=digest,
        files=(SourceFileIdentity(relative_path="meta/info.json", size_bytes=2, sha256=digest),),
    )
    return ReviewDecision(
        decision_id=decision_id,
        decision=value,
        reason_codes=("quality-reviewed",),
        actor_id="reviewer",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        source=source,
        annotation_revision_id="annotation-1",
        edit_revision_id="edit-1",
        quality_run_id="quality-1",
    )


def _quality_report(decision: ReviewDecision, *, run_id: str | None = None) -> QualityReport:
    return QualityReport(
        run_id=run_id or decision.quality_run_id,
        check_set_version="1.0.0",
        source=decision.source,
        actor_id="reviewer",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        episode_checks=(
            QualityCheckResult(
                check_id="source.identity",
                required=True,
                outcome=QualityOutcome.PASS,
            ),
        ),
        package_checks=(),
    )


def _package_quality() -> PackageQualityReport:
    return PackageQualityReport(
        target_format=ReleaseFormat(name="lerobot", version="3.0"),
        episode_count=1,
        frame_count=1,
        episode_frame_counts={0: 1},
        features=("action",),
        nonvisual_rows_read_back=1,
        visual_samples=(),
        inventory_verified=True,
        checksums_verified=True,
    )


def _manifest(
    accepted: ReviewDecision,
    *,
    rejected: tuple[ReviewDecision, ...] = (),
    excluded: tuple[EligibilityCandidate, ...] = (),
) -> ReleaseManifest:
    return ReleaseManifest(
        release_id="release-1",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        actor_id="publisher",
        source_provenance=(accepted.source,),
        accepted_decision_ids=(accepted.decision_id,),
        rejected_decision_ids=tuple(decision.decision_id for decision in rejected),
        excluded_episode_indices=tuple(candidate.episode_index for candidate in excluded),
        episode_index_mapping={0: 0},
        source_formats=(ReleaseFormat(name="lerobot", version="3.0"),),
        target_format=ReleaseFormat(name="lerobot", version="3.0"),
        adapter_versions={"writer": "1.0.0"},
        tool_versions={"dataviewer": "0.1.0"},
        feature_schema={"action": {"dtype": "float32", "shape": [1]}},
        candidate_count=1 + len(rejected) + len(excluded),
        accepted_count=1,
        rejected_count=len(rejected),
        excluded_count=len(excluded),
        nonincluded_count=len(rejected) + len(excluded),
        episode_count=1,
        frame_count=1,
        quality_evidence=(
            QualityEvidenceReference(
                source_episode_index=0,
                release_episode_index=0,
                decision_id=accepted.decision_id,
                quality_run_id=accepted.quality_run_id,
                quality_report_path=f"metadata/quality/{accepted.quality_run_id}.json",
                check_set_version="1.0.0",
                required_outcome=QualityOutcome.PASS,
            ),
        ),
        files=(),
    )


def test_given_staging_package_when_finalized_then_ledgers_manifest_and_checksums_verify(tmp_path: Path) -> None:
    # Arrange
    package_root = tmp_path / "staging"
    data_path = package_root / "data" / "episode.parquet"
    data_path.parent.mkdir(parents=True)
    data_path.write_bytes(b"episode-data")
    statistics_path = package_root / "metadata" / "release-statistics.json"
    statistics_path.parent.mkdir(parents=True)
    statistics_path.write_bytes(b'{"schema_version":"1.0.0"}\n')
    accepted = _decision(ReviewDecisionValue.ACCEPT, "decision-accepted")
    rejected = _decision(ReviewDecisionValue.REJECT, "decision-rejected", episode_index=1)
    excluded = EligibilityCandidate(
        episode_index=2,
        disposition="excluded",
        reason_codes=("unreviewed",),
    )

    # Act
    finalized = finalize_release(
        package_root,
        _manifest(accepted, rejected=(rejected,), excluded=(excluded,)),
        (accepted,),
        (rejected,),
        (_quality_report(accepted),),
        _package_quality(),
        (excluded,),
    )

    # Assert
    assert verify_release(package_root) == finalized
    assert {entry.path for entry in finalized.files} == {
        "data/episode.parquet",
        "metadata/accepted.json",
        "metadata/excluded.json",
        "metadata/package-quality.json",
        "metadata/quality/quality-1.json",
        "metadata/rejected.json",
        "metadata/release-statistics.json",
    }
    assert finalized.candidate_count == 3
    assert finalized.nonincluded_count == 2
    checksum_paths = {
        line.split("  ", maxsplit=1)[1]
        for line in (package_root / "checksums.sha256").read_text(encoding="utf-8").splitlines()
    }
    assert checksum_paths == {"metadata/release-manifest.json", *(entry.path for entry in finalized.files)}


def test_given_modified_covered_file_when_verified_then_digest_mismatch_is_rejected(tmp_path: Path) -> None:
    # Arrange
    package_root = tmp_path / "staging"
    package_root.mkdir()
    (package_root / "payload.bin").write_bytes(b"original")
    accepted = _decision(ReviewDecisionValue.ACCEPT, "decision-accepted")
    finalize_release(
        package_root,
        _manifest(accepted),
        (accepted,),
        (),
        (_quality_report(accepted),),
        _package_quality(),
    )
    (package_root / "payload.bin").write_bytes(b"modified")

    # Act & Assert
    try:
        verify_release(package_root)
    except ValueError as exc:
        assert "SHA-256 mismatch" in str(exc)
    else:
        raise AssertionError("Modified release file was accepted")


def test_given_symlink_when_finalized_then_release_is_rejected(tmp_path: Path) -> None:
    # Arrange
    package_root = tmp_path / "staging"
    data_path = package_root / "data" / "episode.parquet"
    data_path.parent.mkdir(parents=True)
    data_path.write_bytes(b"episode-data")
    link_path = package_root / "meta" / "videos" / "episode.mp4"
    link_path.parent.mkdir(parents=True)
    link_path.symlink_to(data_path)
    accepted = _decision(ReviewDecisionValue.ACCEPT, "decision-accepted")

    # Act & Assert
    with pytest.raises(ValueError, match="symbolic links"):
        finalize_release(
            package_root,
            _manifest(accepted),
            (accepted,),
            (),
            (_quality_report(accepted),),
            _package_quality(),
        )


def test_given_symlink_added_after_finalization_when_verified_then_release_is_rejected(tmp_path: Path) -> None:
    # Arrange
    package_root = tmp_path / "staging"
    payload_path = package_root / "payload.bin"
    package_root.mkdir()
    payload_path.write_bytes(b"original")
    accepted = _decision(ReviewDecisionValue.ACCEPT, "decision-accepted")
    finalize_release(
        package_root,
        _manifest(accepted),
        (accepted,),
        (),
        (_quality_report(accepted),),
        _package_quality(),
    )
    external_path = tmp_path / "external.bin"
    external_path.write_bytes(b"original")
    payload_path.unlink()
    payload_path.symlink_to(external_path)

    # Act & Assert
    with pytest.raises(ValueError, match="symbolic links"):
        verify_release(package_root)


def test_given_mismatched_quality_report_when_finalized_then_release_is_rejected(tmp_path: Path) -> None:
    # Arrange
    package_root = tmp_path / "staging"
    package_root.mkdir()
    (package_root / "payload.bin").write_bytes(b"original")
    accepted = _decision(ReviewDecisionValue.ACCEPT, "decision-accepted")

    # Act & Assert
    with pytest.raises(ValueError, match="quality_run_id"):
        finalize_release(
            package_root,
            _manifest(accepted),
            (accepted,),
            (),
            (_quality_report(accepted, run_id="quality-other"),),
            _package_quality(),
        )
