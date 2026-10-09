"""Revision-validated saved inputs shared by judge entry points."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from evaluation.vlm_judge.dataset import EpisodeRecord
from evaluation.vlm_judge.saved_input import (
    SavedInputError,
    SavedInputSnapshot,
    SavedResource,
    resolve_validation_sample,
)
from evaluation.vlm_judge.saved_input import resolve_saved_input as resolve_snapshot

from .annotation_service import AnnotationService
from .dataset_service import DatasetService


class ViewerSavedInputReader:
    def __init__(self, datasets: DatasetService, annotations: AnnotationService) -> None:
        self.datasets = datasets
        self.annotations = annotations

    async def annotation(self, dataset_id: str, episode_index: int) -> SavedResource:
        resource = await self.annotations.get_annotation_versioned(dataset_id, episode_index)
        return SavedResource(resource.value.model_dump(mode="json") if resource.value else None, resource.etag)

    async def edits(
        self, dataset_id: str, episode_index: int, principal_scope_id: str, source: tuple[str, str]
    ) -> SavedResource:
        resource = await self.annotations.get_saved_edits(
            dataset_id, episode_index, author_id=principal_scope_id, source_id=source[0], source_revision=source[1]
        )
        return SavedResource(resource.value.model_dump(mode="json") if resource.value else None, resource.etag)

    async def source_revision(self, dataset_id: str, episode_index: int) -> tuple[str, str]:
        return await self.datasets.get_source_revision(dataset_id, episode_index)


class ViewerDatasetResolver:
    """Resolve saved viewer inputs without materializing media or importing routers."""

    def __init__(self, datasets: DatasetService, annotations: AnnotationService) -> None:
        self.datasets = datasets
        self.annotations = annotations

    async def validation_sample(
        self,
        dataset_id: str,
        episode_index: int,
        *,
        principal_scope_id: str,
        reference: dict[str, str],
        views: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        _, snapshot = await self.resolve(
            dataset_id,
            episode_index,
            principal_scope_id=principal_scope_id,
            views=views,
            annotation_author_id=reference.get("annotation_author_id"),
            expected_snapshot_id=reference.get("snapshot_id"),
        )
        return await resolve_validation_sample(
            ViewerSavedInputReader(self.datasets, self.annotations), snapshot, reference
        )

    async def episode_indices(self, dataset_id: str, *, principal_scope_id: str) -> list[int]:
        if await self.datasets.get_dataset(dataset_id) is None:
            raise SavedInputError("Dataset not found", 404)
        episodes = await self.datasets.list_episodes(dataset_id, limit=10001, require_actual=True)
        if len(episodes) > 10000:
            raise SavedInputError("Judge selection supports at most 10000 episodes", 422)
        return sorted({episode.index for episode in episodes})

    async def resolve(
        self,
        dataset_id: str,
        episode_index: int,
        *,
        principal_scope_id: str,
        views: tuple[str, ...] = (),
        annotation_author_id: str | None = None,
        expected_snapshot_id: str | None = None,
    ) -> tuple[EpisodeRecord, SavedInputSnapshot]:
        if await self.datasets.get_dataset(dataset_id) is None:
            raise SavedInputError("Dataset not found", 404)
        source = await self.datasets.get_source_revision(dataset_id, episode_index)
        record = await self.datasets.get_episode_media_record(dataset_id, episode_index)
        if record is None:
            raise SavedInputError("Episode not found", 404)
        snapshot = await resolve_saved_input(
            self.datasets,
            self.annotations,
            dataset_id,
            episode_index,
            principal_scope_id=principal_scope_id,
            source=source,
            dataset_instruction=record.instruction,
            media_identity=record.media_identity,
            video_windows=record.video_windows,
            annotation_author_id=annotation_author_id,
            expected_snapshot_id=expected_snapshot_id,
        )
        verified = await self.datasets.get_episode_media_record(dataset_id, episode_index)
        if verified is None or (verified.media_identity, verified.video_windows, verified.instruction) != (
            record.media_identity,
            record.video_windows,
            record.instruction,
        ):
            raise SavedInputError("Media changed while resolving saved inputs")
        if views:
            if not set(views).issubset(record.video_paths):
                raise SavedInputError("Unknown camera selection", 422)
            record = replace(
                record,
                video_paths={view: record.video_paths[view] for view in views},
                video_windows={view: window for view, window in record.video_windows.items() if view in views},
                media_identity={view: record.media_identity[view] for view in views} if record.media_identity else None,
            )
        return replace(
            record,
            episode_id=f"{dataset_id}/episode_{episode_index:06d}",
            instruction=snapshot.instruction,
            snapshot_id=snapshot.snapshot_id,
        ), snapshot


async def resolve_saved_input(
    datasets: DatasetService,
    annotations: AnnotationService,
    dataset_id: str,
    episode_index: int,
    *,
    principal_scope_id: str,
    source: tuple[str, str],
    dataset_instruction: str,
    media_identity: dict[str, str] | None = None,
    video_windows: dict[str, tuple[float, float]] | None = None,
    annotation_author_id: str | None = None,
    expected_snapshot_id: str | None = None,
) -> SavedInputSnapshot:
    return await resolve_snapshot(
        ViewerSavedInputReader(datasets, annotations),
        dataset_id,
        episode_index,
        principal_scope_id=principal_scope_id,
        source=source,
        dataset_instruction=dataset_instruction,
        media_identity=media_identity,
        video_windows=video_windows,
        annotation_author_id=annotation_author_id,
        expected_snapshot_id=expected_snapshot_id,
    )


__all__ = ["SavedInputError", "SavedInputSnapshot", "ViewerSavedInputReader", "resolve_saved_input"]
