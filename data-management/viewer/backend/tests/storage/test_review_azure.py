"""Behavior tests for append-only Azure review persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.models.reviews import AnnotationRevision, SourceFileIdentity, SourceIdentity
from src.api.storage.review_azure import AzureReviewRepository
from src.api.storage.review_base import DuplicateReviewRecordError, ReviewStorageError


def _revision() -> AnnotationRevision:
    source = SourceIdentity(
        dataset_id="sample-dataset",
        episode_index=7,
        source_format="lerobot",
        format_version="3.0",
        source_digest="a" * 64,
        files=(SourceFileIdentity(relative_path="data/episode.parquet", size_bytes=42, sha256="b" * 64),),
    )
    return AnnotationRevision(
        revision_id="annotation-01",
        source=source,
        actor_id="localuser",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        annotation={},
    )


async def test_given_revision_when_created_then_blob_is_create_only() -> None:
    # Arrange
    container = MagicMock()
    blob = MagicMock()
    blob.upload_blob = AsyncMock()
    container.get_blob_client.return_value = blob
    repository = AzureReviewRepository(container_client=container, export_prefix="dataset-exports")

    # Act
    await repository.create_annotation_revision(_revision())

    # Assert
    container.get_blob_client.assert_called_once_with(
        "dataset-exports/reviews/sample-dataset/episodes/episode-000007/annotations/annotation-01.json",
    )
    assert blob.upload_blob.await_args.kwargs["overwrite"] is False


async def test_given_storage_collision_when_created_then_duplicate_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    class ResourceExistsError(Exception):
        pass

    container = MagicMock()
    blob = MagicMock()
    blob.upload_blob = AsyncMock(side_effect=ResourceExistsError("exists"))
    container.get_blob_client.return_value = blob
    monkeypatch.setattr("src.api.storage.review_azure.ResourceExistsError", ResourceExistsError)
    repository = AzureReviewRepository(container_client=container, export_prefix="dataset-exports")

    # Act & Assert
    with pytest.raises(DuplicateReviewRecordError, match="annotation-01"):
        await repository.create_annotation_revision(_revision())


@pytest.mark.parametrize("prefix", ["/absolute", "../escape", "folder/../escape", "folder\\escape"])
def test_given_unsafe_export_prefix_when_created_then_rejected(prefix: str) -> None:
    # Act & Assert
    with pytest.raises(ReviewStorageError, match="prefix"):
        AzureReviewRepository(container_client=MagicMock(), export_prefix=prefix)
