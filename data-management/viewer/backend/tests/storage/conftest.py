"""Shared test fixtures for storage adapter tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from src.api.models.annotations import (
    AnomalyAnnotation,
    ConfidenceLevel,
    DataQualityAnnotation,
    DataQualityLevel,
    EpisodeAnnotation,
    EpisodeAnnotationFile,
    QualityScore,
    TaskCompletenessAnnotation,
    TaskCompletenessRating,
    TrajectoryQualityAnnotation,
    TrajectoryQualityMetrics,
)

if TYPE_CHECKING:
    from src.api.storage.blob_dataset import BlobDatasetProvider
    from src.api.storage.local import LocalStorageAdapter


@pytest.fixture
def dataset_id() -> str:
    """Return the dataset identifier shared by storage adapter tests."""
    return "test-dataset"


@pytest.fixture
def huggingface_repo_id() -> str:
    """Return the mocked Hugging Face repository identifier."""
    return "lerobot/test-dataset"


@pytest.fixture
def local_storage_adapter(tmp_path: Path) -> LocalStorageAdapter:
    """Create a local storage adapter rooted in pytest's temporary directory."""
    from src.api.storage.local import LocalStorageAdapter

    return LocalStorageAdapter(tmp_path)


def create_blob_dataset_provider(mock_client: object | None = None) -> BlobDatasetProvider:
    """Create a Blob dataset provider with an optional injected client."""
    from src.api.storage.blob_dataset import BlobDatasetProvider

    provider = BlobDatasetProvider(
        account_name="testaccount",
        container_name="testcontainer",
        sas_token="sas-token",
    )
    if mock_client is not None:
        provider._client = mock_client
    return provider


def create_test_annotation(episode_index: int, user_id: str = "test-user") -> EpisodeAnnotationFile:
    """Create a test annotation file."""
    now = datetime.now(UTC)
    annotation = EpisodeAnnotation(
        annotator_id=user_id,
        timestamp=now,
        task_completeness=TaskCompletenessAnnotation(
            rating=TaskCompletenessRating.SUCCESS,
            confidence=ConfidenceLevel.FIVE,
            completion_percentage=100,
        ),
        trajectory_quality=TrajectoryQualityAnnotation(
            overall_score=QualityScore.FIVE,
            metrics=TrajectoryQualityMetrics(
                smoothness=QualityScore.FIVE,
                efficiency=QualityScore.FIVE,
                safety=QualityScore.FIVE,
                precision=QualityScore.FIVE,
            ),
        ),
        data_quality=DataQualityAnnotation(overall_quality=DataQualityLevel.GOOD),
        anomalies=AnomalyAnnotation(),
        notes="Test annotation",
    )
    return EpisodeAnnotationFile(
        schema_version="1.0",
        episode_index=episode_index,
        dataset_id="test-dataset",
        annotations=[annotation],
    )
