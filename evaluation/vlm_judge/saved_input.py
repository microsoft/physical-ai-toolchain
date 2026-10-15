"""Resolve evaluator media from operator-configured dataset roots."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from physical_ai_curation.adapters.local_saved_input import LocalSavedInputReader
from physical_ai_curation.saved_input import (
    SavedInputError,
    SavedInputSnapshot,
    resolve_saved_input,
    resolve_validation_sample,
)

if TYPE_CHECKING:
    from .dataset import EpisodeRecord


class LocalDatasetResolver:
    """Resolve identifiers only from an operator-configured dataset allowlist."""

    def __init__(self, datasets: dict[str, Path], *, instruction_override: str | None = None) -> None:
        self._root_aliases = {identifier: root.absolute() for identifier, root in datasets.items()}
        self.datasets = {identifier: root.resolve() for identifier, root in datasets.items()}
        self.instruction_override = instruction_override

    def _video_validator(self, dataset_id: str) -> Callable[[Path], Path]:
        from .frames import validate_media_root, validate_video_path

        try:
            root = validate_media_root(self.datasets[dataset_id])
        except (OSError, RuntimeError, ValueError) as error:
            raise SavedInputError("Judge unavailable", 503) from error

        def validate(path: Path) -> Path:
            try:
                return validate_video_path(path, root, root_alias=self._root_aliases[dataset_id])
            except (OSError, RuntimeError, ValueError) as error:
                raise SavedInputError("Invalid media input", 400) from error

        return validate

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
        from physical_ai_curation.adapters.local_storage import LocalCurationStorage

        labels = await LocalCurationStorage(self.datasets).load_versioned(dataset_id)
        return await resolve_validation_sample(
            LocalSavedInputReader(self.datasets[dataset_id]), snapshot, reference, labels=labels.value
        )

    async def episode_indices(self, dataset_id: str, *, principal_scope_id: str) -> list[int]:
        from .dataset import iter_episodes

        root = self.datasets.get(dataset_id)
        if root is None:
            raise SavedInputError("Dataset is not configured", 404)
        return await asyncio.to_thread(
            lambda: sorted(
                {
                    record.episode_index
                    for record in iter_episodes(root, video_validator=self._video_validator(dataset_id))
                }
            )
        )

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
        from dataclasses import replace

        from .dataset import iter_episodes, load_dataset_spec

        root = self.datasets.get(dataset_id)
        if root is None:
            raise SavedInputError("Dataset is not configured", 404)
        reader = LocalSavedInputReader(root)
        source = await reader.source_revision(dataset_id, episode_index)
        specification = await asyncio.to_thread(load_dataset_spec, root)
        if set(views) - set(specification.video_keys):
            raise SavedInputError("Requested camera is unavailable", 422)
        records = await asyncio.to_thread(
            list,
            iter_episodes(
                root,
                views=views or None,
                indices=[episode_index],
                video_validator=self._video_validator(dataset_id),
            ),
        )
        if len(records) != 1:
            raise SavedInputError("Episode is unavailable", 404)
        record = records[0]
        snapshot = await resolve_saved_input(
            reader,
            dataset_id,
            episode_index,
            principal_scope_id=principal_scope_id,
            source=source,
            dataset_instruction=record.instruction,
            media_identity=record.media_identity,
            video_windows=record.video_windows,
            annotation_author_id=annotation_author_id,
            expected_snapshot_id=expected_snapshot_id,
            declared_instruction_override=self.instruction_override,
        )
        return replace(
            record,
            episode_id=f"{dataset_id}/episode_{episode_index:06d}",
            instruction=snapshot.instruction,
            snapshot_id=snapshot.snapshot_id,
        ), snapshot
