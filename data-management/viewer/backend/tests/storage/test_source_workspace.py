"""Behavior tests for read-only source workspaces."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.api.storage.source_workspace import AzureSourceWorkspace, LocalSourceWorkspace


async def test_given_local_source_when_opened_then_configured_dataset_root_is_used(tmp_path: Path) -> None:
    # Arrange
    dataset_root = tmp_path / "datasets" / "dataset-1"
    dataset_root.mkdir(parents=True)
    workspace = LocalSourceWorkspace(tmp_path / "datasets")

    # Act
    async with workspace.open("dataset-1") as opened_root:
        observed = opened_root

    # Assert
    assert observed == dataset_root.resolve()


async def test_given_azure_source_when_opened_then_complete_workspace_is_cleaned_up(tmp_path: Path) -> None:
    # Arrange
    provider = MagicMock()

    async def materialize(_dataset_id: str, target: Path) -> bool:
        (target / "videos").mkdir(parents=True)
        (target / "videos" / "episode.mp4").write_bytes(b"video")
        return True

    provider.materialize_dataset_to_local = AsyncMock(side_effect=materialize)
    workspace = AzureSourceWorkspace(provider, temporary_root=tmp_path)

    # Act
    async with workspace.open("dataset-1") as opened_root:
        materialized_root = opened_root
        materialized_video = opened_root / "videos" / "episode.mp4"
        assert materialized_video.is_file()

    # Assert
    provider.materialize_dataset_to_local.assert_awaited_once()
    assert not materialized_root.exists()
