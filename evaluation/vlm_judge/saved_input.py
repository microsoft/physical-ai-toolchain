"""Storage-neutral saved-input resolution for judge clients."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from .dataset import EpisodeRecord


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
    async def annotation(self, dataset_id: str, episode_index: int) -> SavedResource: ...

    async def edits(
        self, dataset_id: str, episode_index: int, principal_scope_id: str, source: tuple[str, str]
    ) -> SavedResource: ...

    async def source_revision(self, dataset_id: str, episode_index: int) -> tuple[str, str]: ...


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


def local_source_revision(root: Path, metadata_paths: list[Path] | None = None) -> tuple[str, str]:
    """Fingerprint source metadata without including editable curation resources."""
    root = root.resolve()
    paths = (
        metadata_paths
        if metadata_paths is not None
        else sorted(
            path
            for path in (root / "meta").rglob("*")
            if path.is_file() and path.suffix in {".json", ".jsonl", ".parquet"} and path.name != "episode_labels.json"
        )
    )
    if not paths:
        raise SavedInputError("Source generation is unavailable")
    versions = []
    for path in paths:
        path = path.resolve()
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        versions.append([relative, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])
    return (
        hashlib.sha256(f"local:{root}".encode()).hexdigest(),
        hashlib.sha256(json.dumps(versions).encode()).hexdigest(),
    )


async def resolve_validation_sample(
    reader: SavedInputReader,
    snapshot: SavedInputSnapshot,
    reference: dict[str, str],
) -> dict[str, Any]:
    from .curation import ContributionLedger

    if (
        set(reference) != {"annotation_author_id", "annotation_revision", "snapshot_id"}
        or any(not isinstance(value, str) or not value.strip() for value in reference.values())
        or snapshot.annotation_author_id != reference["annotation_author_id"]
        or snapshot.annotation_revision != reference["annotation_revision"]
        or snapshot.snapshot_id != reference["snapshot_id"]
        or snapshot.instruction_origin != "annotation"
    ):
        raise SavedInputError("Select explicit saved human instruction and outcome revisions")
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
        raise SavedInputError("Select one saved human annotation for each sample")
    annotation = selected[0]
    instruction = annotation.get("language_instruction") or {}
    outcome = (annotation.get("task_completeness") or {}).get("rating")
    if (
        instruction.get("source") != "human"
        or instruction.get("instruction") != snapshot.instruction
        or outcome not in {"success", "failure", "partial"}
    ):
        raise SavedInputError("Samples require a saved human instruction and known human outcome")
    ledger = ContributionLedger.model_validate(value.get("provenance", {}).get(snapshot.annotation_author_id, {}))
    for field, expected in (
        ("language_instruction/instruction", snapshot.instruction),
        ("task_completeness/rating", outcome),
    ):
        resolved = ledger.resolve(field, human_author_id=snapshot.annotation_author_id)
        if resolved.origin != "human" or resolved.value != expected:
            raise SavedInputError(
                "Sample instruction and outcome require human authorship, not acceptance or legacy ownership"
            )
    if (await reader.annotation(snapshot.dataset_id, snapshot.episode_index)).etag != resource.etag:
        raise SavedInputError("Validation sample revision changed")
    return {"reference": dict(reference), "input": snapshot.model_dump(mode="json"), "human_outcome": outcome}


class LocalSavedInputReader:
    """Read the documented local curation layout beneath one configured dataset root."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _read(self, relative: Path) -> SavedResource:
        path = (self.root / relative).resolve()
        path.relative_to(self.root)
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            return SavedResource(None, None)
        value = json.loads(content)
        if not isinstance(value, dict):
            raise SavedInputError("Invalid saved curation resource")
        return SavedResource(value, f'"{hashlib.sha256(content).hexdigest()}"')

    async def annotation(self, dataset_id: str, episode_index: int) -> SavedResource:
        return await asyncio.to_thread(self._read, Path("annotations/episodes") / f"episode_{episode_index:06d}.json")

    async def edits(
        self, dataset_id: str, episode_index: int, principal_scope_id: str, source: tuple[str, str]
    ) -> SavedResource:
        namespace = hashlib.sha256(
            json.dumps([source[0], principal_scope_id], separators=(",", ":")).encode()
        ).hexdigest()
        resource = await asyncio.to_thread(
            self._read, Path("annotations/edits") / namespace / f"episode_{episode_index:06d}.json"
        )
        descriptor = (resource.value or {}).get("saved_edits", {}).get(source[0], {}).get(principal_scope_id)
        if descriptor:
            if (
                descriptor.get("dataset_id"),
                descriptor.get("episode_index"),
                descriptor.get("source_id"),
                descriptor.get("author_id"),
            ) != (dataset_id, episode_index, source[0], principal_scope_id):
                raise SavedInputError("Saved edit scope mismatch")
            if descriptor.get("source_revision") != source[1]:
                raise SavedInputError("Saved edit source revision changed")
        return SavedResource(descriptor, resource.etag)

    async def source_revision(self, dataset_id: str, episode_index: int) -> tuple[str, str]:
        return await asyncio.to_thread(local_source_revision, self.root)


class LocalDatasetResolver:
    """Resolve identifiers only from an operator-configured dataset allowlist."""

    def __init__(self, datasets: dict[str, Path], *, instruction_override: str | None = None) -> None:
        self.datasets = {identifier: root.resolve() for identifier, root in datasets.items()}
        self.instruction_override = instruction_override

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
        return await resolve_validation_sample(LocalSavedInputReader(self.datasets[dataset_id]), snapshot, reference)

    async def episode_indices(self, dataset_id: str, *, principal_scope_id: str) -> list[int]:
        from .dataset import iter_episodes

        root = self.datasets.get(dataset_id)
        if root is None:
            raise SavedInputError("Dataset is not configured", 404)
        return await asyncio.to_thread(lambda: sorted({record.episode_index for record in iter_episodes(root)}))

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
        records = await asyncio.to_thread(list, iter_episodes(root, views=views or None, indices=[episode_index]))
        if len(records) != 1:
            raise SavedInputError("Episode is unavailable", 404)
        record = records[0]
        for path in record.video_paths.values():
            path.resolve().relative_to(root)
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
