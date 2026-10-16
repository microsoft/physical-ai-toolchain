"""Storage-neutral saved-input resolution for judge clients."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from physical_ai_curation.models import DatasetLabelsFile


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
    instruction_origin: Literal["annotation", "dataset", "cli-override"]


@dataclass(frozen=True)
class SavedResource:
    value: dict[str, Any] | None
    etag: str | None


class SavedInputReader(Protocol):
    async def annotation(self, dataset_id: str, episode_index: int) -> SavedResource:
        """Read the saved episode annotation and its revision."""

    async def edits(
        self, dataset_id: str, episode_index: int, principal_scope_id: str, source: tuple[str, str]
    ) -> SavedResource:
        """Read saved edits for the caller and source identity."""

    async def source_revision(self, dataset_id: str, episode_index: int) -> tuple[str, str]:
        """Return the current source identity and revision."""


def _transforms_media(transform: dict[str, Any] | None) -> bool:
    if not transform:
        return False
    return bool(
        transform.get("crop")
        or transform.get("resize")
        or transform.get("colorFilter") not in (None, "none")
        or any(
            value is not None and value != (1 if name == "gamma" else 0)
            for name, value in (transform.get("colorAdjustment") or {}).items()
        )
    )


async def resolve_saved_input(
    reader: SavedInputReader,
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
    declared_instruction_override: str | None = None,
) -> SavedInputSnapshot:
    """Bind saved instructions and supported edits to revalidated resource revisions."""
    annotation = await reader.annotation(dataset_id, episode_index)
    edits = await reader.edits(dataset_id, episode_index, principal_scope_id, source)
    candidates = [
        entry for entry in (annotation.value or {}).get("annotations", []) if entry.get("language_instruction")
    ]
    if annotation_author_id is not None:
        candidates = [entry for entry in candidates if entry.get("annotator_id") == annotation_author_id]
        if not candidates:
            raise SavedInputError("The selected saved annotation is unavailable", 404)
    if len(candidates) > 1:
        raise SavedInputError("Select an explicit saved annotation author; instructions are ambiguous")
    selected = candidates[0] if candidates else None
    instruction = selected["language_instruction"]["instruction"] if selected else dataset_instruction
    origin = "annotation" if selected else "dataset"
    if declared_instruction_override is not None:
        instruction, origin = declared_instruction_override, "cli-override"
    if not isinstance(instruction, str) or not instruction.strip():
        raise SavedInputError("Save a task instruction before judging", 422)
    if edits.value:
        operations = edits.value["operations"]
        if (
            operations.get("removedFrames")
            or operations.get("insertedFrames")
            or operations.get("trajectoryAdjustments")
            or _transforms_media(operations.get("globalTransform"))
            or any(_transforms_media(transform) for transform in (operations.get("cameraTransforms") or {}).values())
        ):
            raise SavedInputError("Saved media edits cannot be rendered for judging; clear and save them first", 422)
    annotation_after = await reader.annotation(dataset_id, episode_index)
    edits_after = await reader.edits(dataset_id, episode_index, principal_scope_id, source)
    if (
        await reader.source_revision(dataset_id, episode_index) != source
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
        "annotation_author_id": selected["annotator_id"] if selected else None,
        "annotation_revision": annotation.etag,
        "edit_revision": edits.etag,
        "instruction": instruction,
        "instruction_origin": origin,
    }
    content = {
        **identity,
        "schema_version": 1,
        "media_identity": media_identity,
        "video_windows": video_windows,
        "annotation": selected,
        "edits": edits.value,
    }
    snapshot_id = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if expected_snapshot_id is not None and snapshot_id != expected_snapshot_id:
        raise SavedInputError("Saved snapshot is stale or belongs to another scope and cannot be reproduced")
    return SavedInputSnapshot(snapshot_id=snapshot_id, **identity)


SAMPLE_OUTCOMES = frozenset({"success", "failure", "partial"})


def label_baseline(labels: DatasetLabelsFile, episode_index: int) -> dict[str, str] | None:
    """Resolve one saved outcome label with an episode-scoped revision, regardless of its origin."""
    key = str(episode_index)
    outcomes = {label.lower() for label in labels.episodes.get(key, []) if label.lower() in SAMPLE_OUTCOMES}
    if len(outcomes) != 1:
        return None
    ledger = labels.provenance.get(key)
    content = {
        "dataset_id": labels.dataset_id,
        "episode_index": episode_index,
        "outcomes": sorted(outcomes),
        "contributions": [
            entry.model_dump(mode="json")
            for entry in (ledger.contributions if ledger else [])
            if entry.field.lower() in {f"labels/{outcome}" for outcome in SAMPLE_OUTCOMES}
        ],
    }
    return {
        "outcome": next(iter(outcomes)),
        "revision": hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest(),
    }


def grounding_issue(value: dict[str, Any], annotation: dict[str, Any]) -> str | None:
    """Explain why one saved annotation cannot ground judge calibration, or return None."""
    instruction = annotation.get("language_instruction") or {}
    text = instruction.get("instruction")
    outcome = (annotation.get("task_completeness") or {}).get("rating")
    if not isinstance(text, str) or not text.strip() or outcome not in SAMPLE_OUTCOMES:
        return "Samples require a saved instruction and known baseline outcome"
    return None


def _saved_at(annotation: dict[str, Any]) -> datetime:
    try:
        saved = datetime.fromisoformat(str(annotation.get("timestamp")))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return saved if saved.tzinfo else saved.replace(tzinfo=UTC)


def grounding_author(value: dict[str, Any] | None) -> str | None:
    """Return the author of the latest saved annotation with an instruction and known outcome."""
    usable = [
        annotation
        for annotation in (value or {}).get("annotations", [])
        if grounding_issue(value or {}, annotation) is None
    ]
    return max(usable, key=_saved_at)["annotator_id"] if usable else None


async def resolve_validation_sample(
    reader: SavedInputReader,
    snapshot: SavedInputSnapshot,
    reference: dict[str, str],
    *,
    labels: DatasetLabelsFile | None = None,
) -> dict[str, Any]:
    annotation_keys = {"annotation_author_id", "annotation_revision"}
    label_sample = "labels_revision" in reference
    required = {"snapshot_id", "labels_revision"} if label_sample else {"snapshot_id", *annotation_keys}
    if (
        not required.issubset(reference)
        or set(reference) - required - annotation_keys
        or (bool(annotation_keys & reference.keys()) and not annotation_keys.issubset(reference))
        or any(not isinstance(value, str) or not value.strip() for value in reference.values())
        or ("annotation_author_id" in reference and snapshot.annotation_author_id != reference["annotation_author_id"])
        or ("annotation_revision" in reference and snapshot.annotation_revision != reference["annotation_revision"])
        or snapshot.snapshot_id != reference["snapshot_id"]
        or not snapshot.instruction.strip()
    ):
        raise SavedInputError("Select current saved instruction and baseline revisions")
    if label_sample:
        baseline = label_baseline(labels, snapshot.episode_index) if labels else None
        if labels is None or labels.dataset_id != snapshot.dataset_id or baseline is None:
            raise SavedInputError("Select exactly one saved success, failure or partial label")
        if baseline["revision"] != reference["labels_revision"]:
            raise SavedInputError("Validation label revision changed")
        return {
            "reference": dict(reference),
            "input": snapshot.model_dump(mode="json"),
            "human_outcome": baseline["outcome"],
            "baseline_source": "label",
        }
    resource = await reader.annotation(snapshot.dataset_id, snapshot.episode_index)
    value = resource.value or {}
    if (
        resource.etag != reference["annotation_revision"]
        or value.get("dataset_id") != snapshot.dataset_id
        or value.get("episode_index") != snapshot.episode_index
    ):
        raise SavedInputError("Validation sample revision changed")
    selected = [
        entry for entry in value.get("annotations", []) if entry.get("annotator_id") == snapshot.annotation_author_id
    ]
    if len(selected) != 1:
        raise SavedInputError("Select one saved annotation for each sample")
    annotation = selected[0]
    if (annotation.get("language_instruction") or {}).get("instruction") != snapshot.instruction:
        raise SavedInputError("Samples require a saved instruction and known baseline outcome")
    issue = grounding_issue(value, annotation)
    if issue:
        raise SavedInputError(issue)
    outcome = annotation["task_completeness"]["rating"]
    if (await reader.annotation(snapshot.dataset_id, snapshot.episode_index)).etag != resource.etag:
        raise SavedInputError("Validation sample revision changed")
    return {"reference": dict(reference), "input": snapshot.model_dump(mode="json"), "human_outcome": outcome}
