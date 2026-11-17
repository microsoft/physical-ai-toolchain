"""Revision-validated saved inputs shared by judge entry points."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from physical_ai_curation.adapters.job_storage import BlobJobStore
from physical_ai_curation.adapters.local_saved_input import (
    JudgeEvidenceAuthority,
    JudgeStateReader,
    ScopedJudgeEvidenceAuthority,
    bound_judge_evidence,
    content_etag,
)
from physical_ai_curation.saved_input import (
    SavedInputError,
    SavedInputSnapshot,
    SavedResource,
    label_baseline,
    resolve_validation_sample,
)
from physical_ai_curation.saved_input import resolve_saved_input as resolve_snapshot

from .annotation_service import AnnotationService
from .dataset_service import DatasetService

if TYPE_CHECKING:
    from physical_ai_curation.models import DatasetLabelsFile
    from physical_ai_curation.release.inputs import SavedSelection
    from physical_ai_dataset_io.lerobot import EpisodeRecord

    from ..storage import VersionedValue
    from .label_storage import LabelStorage


class AzureJudgeStateReader:
    """Read persisted judge state from the blob container used by judge jobs."""

    def __init__(self, provider: Any | None) -> None:
        self.provider = provider

    async def read(self) -> dict[str, Any]:
        if self.provider is None:
            raise SavedInputError("Judge evidence storage is unavailable", 503)
        client = await self.provider._get_client()
        blob = client.get_container_client(self.provider.container_name).get_blob_client("_curation/judge/state.json")
        return await BlobJobStore(blob).read()


class ViewerSavedInputReader:
    def __init__(
        self,
        datasets: DatasetService,
        annotations: AnnotationService,
        label_storage: LabelStorage | None = None,
        *,
        judge_evidence: JudgeEvidenceAuthority | None = None,
        cache_release_labels: bool = False,
        cache_source_revisions: bool = False,
    ) -> None:
        self.datasets = datasets
        self.annotations = annotations
        self.label_storage = label_storage
        self.judge_evidence = judge_evidence
        self._cache_release_labels = cache_release_labels
        self._cache_source_revisions = cache_source_revisions
        self._source_revisions: dict[tuple[str, int], tuple[str, str]] = {}
        self._release_labels: dict[str, VersionedValue[DatasetLabelsFile]] = {}
        self._labels_lock = asyncio.Lock()

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

    async def release_resource(self, dataset_id: str, selection: SavedSelection) -> SavedResource:
        """Read selected labels, episode ledger, or bound judge evidence; other kinds are unavailable here."""
        if selection.kind == "evidence":
            if self.judge_evidence is None:
                raise SavedInputError("Judge evidence authority is not bound", 503)
            bound = await self.judge_evidence.evidence(dataset_id, selection.episode_index)
            return bound_judge_evidence(bound, dataset_id, selection.episode_index)
        if selection.kind not in {"labels", "ledger"}:
            return SavedResource(None, None)
        if self.label_storage is None:
            raise SavedInputError("Labels storage is unavailable", 503)
        if self._cache_release_labels:
            async with self._labels_lock:
                if dataset_id not in self._release_labels:
                    self._release_labels[dataset_id] = await self.label_storage.load_versioned(dataset_id)
                resource = self._release_labels[dataset_id]
        else:
            resource = await self.label_storage.load_versioned(dataset_id)
        if resource.value is not None and resource.value.dataset_id != dataset_id:
            raise SavedInputError("Curation dataset identity mismatch")
        if selection.kind == "labels":
            return SavedResource(resource.value.model_dump(mode="json") if resource.value else None, resource.etag)
        ledger = resource.value.provenance.get(str(selection.episode_index)) if resource.value else None
        if ledger is None:
            return SavedResource(None, None)
        value = ledger.model_dump(mode="json")
        return SavedResource(value, content_etag(value))

    async def source_revision(self, dataset_id: str, episode_index: int) -> tuple[str, str]:
        key = (dataset_id, episode_index)
        if self._cache_source_revisions and key in self._source_revisions:
            return self._source_revisions[key]
        revision = await self.datasets.get_source_revision(dataset_id, episode_index)
        if self._cache_source_revisions:
            self._source_revisions[key] = revision
        return revision


class ViewerDatasetResolver:
    """Resolve saved viewer inputs without materializing media or importing routers."""

    def __init__(
        self,
        datasets: DatasetService,
        annotations: AnnotationService,
        *,
        label_storage_factory: Callable[[], LabelStorage],
    ) -> None:
        self.datasets = datasets
        self.annotations = annotations
        self.label_storage_factory = label_storage_factory

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
        storage = self.label_storage_factory()
        labels = await storage.load(dataset_id) if "labels_revision" in reference else None
        sample = await resolve_validation_sample(
            ViewerSavedInputReader(self.datasets, self.annotations), snapshot, reference, labels=labels
        )
        if labels is not None:
            current = await storage.load(dataset_id)
            if label_baseline(current, episode_index) != label_baseline(labels, episode_index):
                raise SavedInputError("Validation label revision changed")
        return sample

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


__all__ = [
    "AzureJudgeStateReader",
    "JudgeStateReader",
    "SavedInputError",
    "SavedInputSnapshot",
    "ScopedJudgeEvidenceAuthority",
    "ViewerSavedInputReader",
    "resolve_saved_input",
]
