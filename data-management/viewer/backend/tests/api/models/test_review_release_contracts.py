"""Behavior tests for immutable review and release contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.api.models.release_workflow import ReleaseSubmitRequest
from src.api.models.releases import (
    OperationalEvent,
    QualityEvidenceReference,
    ReleaseFile,
    ReleaseFormat,
    ReleaseManifest,
    canonical_json_bytes,
)
from src.api.models.reviews import (
    AnnotationRevision,
    QualityCheckResult,
    QualityOutcome,
    QualityReport,
    ReviewDecision,
    ReviewDecisionValue,
    SourceFileIdentity,
    SourceIdentity,
)


@pytest.fixture
def source_identity() -> SourceIdentity:
    return SourceIdentity(
        dataset_id="sample-dataset",
        episode_index=7,
        source_format="lerobot",
        format_version="3.0",
        source_digest="a" * 64,
        files=(SourceFileIdentity(relative_path="data/chunk-000/file-000.parquet", size_bytes=42, sha256="b" * 64),),
    )


def test_given_revision_when_mutated_then_contract_remains_immutable(source_identity: SourceIdentity) -> None:
    # Arrange
    revision = AnnotationRevision(
        revision_id="annotation-01",
        source=source_identity,
        actor_id="reviewer@example.com",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        annotation={"rating": "success"},
    )

    # Act & Assert
    with pytest.raises(ValidationError, match="frozen"):
        revision.actor_id = "other@example.com"


def test_given_self_predecessor_when_revision_validated_then_rejected(source_identity: SourceIdentity) -> None:
    # Act & Assert
    with pytest.raises(ValidationError, match="predecessor"):
        AnnotationRevision(
            revision_id="annotation-01",
            predecessor_revision_id="annotation-01",
            source=source_identity,
            actor_id="localuser",
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
            annotation={},
        )


def test_given_missing_source_identity_when_revision_validated_then_rejected() -> None:
    # Arrange
    payload = {
        "revision_id": "annotation-01",
        "actor_id": "localuser",
        "created_at": datetime(2025, 1, 1, tzinfo=UTC),
        "annotation": {},
    }

    # Act & Assert
    with pytest.raises(ValidationError, match="source"):
        AnnotationRevision.model_validate(payload)


def test_given_naive_timestamp_when_contract_validated_then_rejected(source_identity: SourceIdentity) -> None:
    # Act & Assert
    with pytest.raises(ValidationError, match="UTC"):
        AnnotationRevision(
            revision_id="annotation-01",
            source=source_identity,
            actor_id="localuser",
            created_at=datetime(2025, 1, 1),
            annotation={},
        )


def test_given_review_decision_when_created_then_all_evidence_is_explicit(source_identity: SourceIdentity) -> None:
    # Act
    decision = ReviewDecision(
        decision_id="decision-01",
        decision=ReviewDecisionValue.ACCEPT,
        reason_codes=("quality-approved",),
        notes=None,
        actor_id="reviewer@example.com",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        source=source_identity,
        annotation_revision_id="annotation-01",
        edit_revision_id="edit-01",
        quality_run_id="quality-01",
    )

    # Assert
    assert decision.decision == ReviewDecisionValue.ACCEPT


def test_given_quality_report_when_created_then_outcomes_are_machine_readable(
    source_identity: SourceIdentity,
) -> None:
    # Act
    report = QualityReport(
        run_id="quality-01",
        check_set_version="1.0.0",
        source=source_identity,
        actor_id="localuser",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        episode_checks=(
            QualityCheckResult(
                check_id="timestamps.monotonic",
                required=True,
                outcome=QualityOutcome.PASS,
                measurements={"rows": 42},
                thresholds={},
                reason_codes=(),
            ),
        ),
        package_checks=(),
    )

    # Assert
    assert report.episode_checks[0].outcome == QualityOutcome.PASS


def test_given_multiple_target_formats_when_manifest_validated_then_rejected(
    source_identity: SourceIdentity,
) -> None:
    # Arrange
    payload = {
        "release_id": "release-01",
        "created_at": datetime(2025, 1, 1, tzinfo=UTC),
        "actor_id": "localuser",
        "source_provenance": [source_identity.model_dump(mode="json")],
        "accepted_decision_ids": ["decision-01"],
        "episode_index_mapping": {"7": 0},
        "source_formats": [{"name": "lerobot", "version": "3.0"}],
        "target_format": [
            {"name": "lerobot", "version": "3.0"},
            {"name": "lerobot", "version": "2.1"},
        ],
        "adapter_versions": {"lerobot-v3": "1.0.0"},
        "tool_versions": {"dataviewer": "0.1.0"},
        "feature_schema": {"observation.state": {"dtype": "float32", "shape": [6]}},
        "episode_count": 1,
        "frame_count": 42,
        "files": [],
    }

    # Act & Assert
    with pytest.raises(ValidationError, match="target_format"):
        ReleaseManifest.model_validate(payload)


def test_given_equivalent_manifests_when_serialized_then_canonical_bytes_match(
    source_identity: SourceIdentity,
) -> None:
    # Arrange
    common = {
        "release_id": "release-01",
        "created_at": datetime(2025, 1, 1, tzinfo=UTC),
        "actor_id": "localuser",
        "source_provenance": (source_identity,),
        "accepted_decision_ids": ("decision-01",),
        "rejected_decision_ids": (),
        "excluded_episode_indices": (),
        "episode_index_mapping": {7: 0},
        "source_formats": (ReleaseFormat(name="lerobot", version="3.0"),),
        "target_format": ReleaseFormat(name="lerobot", version="3.0"),
        "candidate_count": 1,
        "accepted_count": 1,
        "rejected_count": 0,
        "excluded_count": 0,
        "nonincluded_count": 0,
        "episode_count": 1,
        "frame_count": 42,
        "quality_evidence": (
            QualityEvidenceReference(
                source_episode_index=7,
                release_episode_index=0,
                decision_id="decision-01",
                quality_run_id="quality-01",
                quality_report_path="metadata/quality/quality-01.json",
                check_set_version="1.0.0",
                required_outcome=QualityOutcome.PASS,
            ),
        ),
        "files": (ReleaseFile(path="data/file.parquet", size_bytes=42, sha256="c" * 64),),
    }
    first = ReleaseManifest(
        **common,
        adapter_versions={"writer": "1.0.0", "reader": "1.0.0"},
        tool_versions={"python": "3.12", "dataviewer": "0.1.0"},
        feature_schema={"action": {"shape": [6], "dtype": "float32"}},
    )
    second = ReleaseManifest(
        **common,
        adapter_versions={"reader": "1.0.0", "writer": "1.0.0"},
        tool_versions={"dataviewer": "0.1.0", "python": "3.12"},
        feature_schema={"action": {"dtype": "float32", "shape": [6]}},
    )

    # Act
    first_bytes = canonical_json_bytes(first)
    second_bytes = canonical_json_bytes(second)

    # Assert
    assert first_bytes == second_bytes
    assert first_bytes.endswith(b"\n")


def test_given_operational_event_when_serialized_then_otel_fields_are_present() -> None:
    # Arrange
    event = OperationalEvent(
        operation_id="operation-01",
        release_id="release-01",
        event_name="release.queued",
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        observed_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        actor_id="localuser",
        status="queued",
        attributes={"destination.kind": "local"},
        body={"episode_count": 1},
    )

    # Act
    payload = event.model_dump(mode="json")

    # Assert
    assert payload["schema_version"] == "1.0.0"
    assert payload["event_name"] == "release.queued"
    assert payload["actor_id"] == "localuser"


@pytest.mark.parametrize("release_id", ["release:2026", "release@production", "release+candidate"])
def test_given_platform_neutral_release_id_when_submitted_then_azureml_delimiters_remain_valid(
    release_id: str,
) -> None:
    request = ReleaseSubmitRequest(
        release_id=release_id,
        dataset_id="sample-dataset",
        actor_id="localuser",
        reason="Publish reviewed episodes",
        destination_kind="local",
        idempotency_key="request-1",
        target_format=ReleaseFormat(name="lerobot", version="3.0"),
    )

    assert request.release_id == release_id
