"""Behavior tests for canonical release artifact finalization."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from src.api.models.releases import ReleaseFormat, ReleaseManifest
from src.api.models.reviews import ReviewDecision, ReviewDecisionValue, SourceFileIdentity, SourceIdentity
from src.api.release.integrity import finalize_release, verify_release


def _decision(value: ReviewDecisionValue, decision_id: str) -> ReviewDecision:
    digest = hashlib.sha256(decision_id.encode()).hexdigest()
    source = SourceIdentity(
        dataset_id="source-dataset",
        episode_index=0,
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


def _manifest(accepted: ReviewDecision) -> ReleaseManifest:
    return ReleaseManifest(
        release_id="release-1",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        actor_id="publisher",
        source_provenance=(accepted.source,),
        accepted_decision_ids=(accepted.decision_id,),
        episode_index_mapping={0: 0},
        source_formats=(ReleaseFormat(name="lerobot", version="3.0"),),
        target_format=ReleaseFormat(name="lerobot", version="3.0"),
        adapter_versions={"writer": "1.0.0"},
        tool_versions={"dataviewer": "0.1.0"},
        feature_schema={"action": {"dtype": "float32", "shape": [1]}},
        episode_count=1,
        frame_count=1,
        files=(),
    )


def test_given_staging_package_when_finalized_then_ledgers_manifest_and_checksums_verify(tmp_path: Path) -> None:
    # Arrange
    package_root = tmp_path / "staging"
    data_path = package_root / "data" / "episode.parquet"
    data_path.parent.mkdir(parents=True)
    data_path.write_bytes(b"episode-data")
    accepted = _decision(ReviewDecisionValue.ACCEPT, "decision-accepted")
    rejected = _decision(ReviewDecisionValue.REJECT, "decision-rejected")

    # Act
    finalized = finalize_release(package_root, _manifest(accepted), (accepted,), (rejected,))

    # Assert
    assert verify_release(package_root) == finalized
    assert {entry.path for entry in finalized.files} == {
        "data/episode.parquet",
        "metadata/accepted.json",
        "metadata/rejected.json",
    }
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
    finalize_release(package_root, _manifest(accepted), (accepted,), ())
    (package_root / "payload.bin").write_bytes(b"modified")

    # Act & Assert
    try:
        verify_release(package_root)
    except ValueError as exc:
        assert "SHA-256 mismatch" in str(exc)
    else:
        raise AssertionError("Modified release file was accepted")
