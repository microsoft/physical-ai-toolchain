"""Revision-validated saved inputs shared by judge entry points."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..models.episode_edits import ImageTransform
from .annotation_service import AnnotationService
from .dataset_service import DatasetService


class SavedInputError(ValueError):
    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


class SavedInputSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    dataset_id: str
    episode_index: int
    principal_scope_id: str
    source_id: str
    source_revision: str
    annotation_author_id: str | None
    annotation_revision: str | None
    edit_revision: str | None
    instruction: str
    instruction_origin: Literal["annotation", "dataset"]


def _transforms_media(transform: ImageTransform | None) -> bool:
    if transform is None:
        return False
    if transform.crop or transform.resize or transform.colorFilter not in (None, "none"):
        return True
    adjustment = transform.colorAdjustment
    return bool(
        adjustment
        and any(
            value is not None and value != (1 if name == "gamma" else 0)
            for name, value in adjustment.model_dump().items()
        )
    )


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
    """Read and recheck independent resources; stale references are not reproducible."""
    annotation = await annotations.get_annotation_versioned(dataset_id, episode_index)
    edits = await annotations.get_saved_edits(
        dataset_id,
        episode_index,
        author_id=principal_scope_id,
        source_id=source[0],
        source_revision=source[1],
    )
    candidates = (
        [entry for entry in annotation.value.annotations if entry.language_instruction] if annotation.value else []
    )
    if annotation_author_id is not None:
        candidates = [entry for entry in candidates if entry.annotator_id == annotation_author_id]
        if not candidates:
            raise SavedInputError("The selected saved annotation is unavailable", 404)
    if len(candidates) > 1:
        raise SavedInputError("Select an explicit saved annotation author; instructions are ambiguous")
    selected = candidates[0] if candidates else None
    instruction = selected.language_instruction.instruction if selected else dataset_instruction
    if not instruction.strip():
        raise SavedInputError("Save a task instruction before judging", 422)
    if edits.value:
        operations = edits.value.operations
        if (
            operations.removedFrames
            or operations.insertedFrames
            or operations.trajectoryAdjustments
            or _transforms_media(operations.globalTransform)
            or any(_transforms_media(transform) for transform in (operations.cameraTransforms or {}).values())
        ):
            raise SavedInputError("Saved media edits cannot be rendered for judging; clear and save them first", 422)
    annotation_after = await annotations.get_annotation_versioned(dataset_id, episode_index)
    edits_after = await annotations.get_saved_edits(
        dataset_id,
        episode_index,
        author_id=principal_scope_id,
        source_id=source[0],
        source_revision=source[1],
    )
    if (
        await datasets.get_source_revision(dataset_id, episode_index) != source
        or annotation_after.etag != annotation.etag
        or edits_after.etag != edits.etag
    ):
        raise SavedInputError("Saved inputs changed while resolving the snapshot; reload and retry")
    identity = {
        "dataset_id": dataset_id,
        "episode_index": episode_index,
        "principal_scope_id": principal_scope_id,
        "source_id": source[0],
        "source_revision": source[1],
        "annotation_author_id": selected.annotator_id if selected else None,
        "annotation_revision": annotation.etag,
        "edit_revision": edits.etag,
        "instruction": instruction,
        "instruction_origin": "annotation" if selected else "dataset",
    }
    content = {
        **identity,
        "schema_version": 1,
        "media_identity": media_identity,
        "video_windows": video_windows,
        "annotation": selected.model_dump(mode="json") if selected else None,
        "edits": edits.value.model_dump(mode="json") if edits.value else None,
    }
    snapshot_id = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if expected_snapshot_id is not None and snapshot_id != expected_snapshot_id:
        raise SavedInputError("Saved snapshot is stale or belongs to another scope and cannot be reproduced")
    return SavedInputSnapshot(snapshot_id=snapshot_id, **identity)
