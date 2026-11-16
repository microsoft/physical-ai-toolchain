"""Filesystem adapter for saved curation resources and source freshness."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from physical_ai_curation.evidence import read_job_evidence
from physical_ai_curation.saved_input import SavedInputError, SavedResource

if TYPE_CHECKING:
    from physical_ai_curation.release.inputs import SavedSelection


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


def content_etag(value: dict[str, Any]) -> str:
    """Return a quoted content revision for one JSON object."""
    return f'"{hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}"'


def bound_judge_evidence(bound: dict[str, Any] | None, dataset_id: str, episode_index: int) -> SavedResource:
    """Validate an explicitly bound judge job/target pair for one episode and return it as a saved resource."""
    if bound is None:
        return SavedResource(None, None)
    job, target = bound.get("job"), bound.get("target")
    if not isinstance(job, dict) or not isinstance(target, dict):
        raise SavedInputError("Invalid judge evidence binding")
    if job.get("dataset_id") != dataset_id or target.get("episode_index") != episode_index:
        raise SavedInputError("Judge evidence binding scope mismatch")
    try:
        read_job_evidence(job, target)
    except (KeyError, TypeError, ValueError) as exc:
        raise SavedInputError("Judge evidence binding is invalid") from exc
    value = {"job": job, "target": target}
    return SavedResource(value, content_etag(value))


class JudgeEvidenceAuthority(Protocol):
    async def evidence(self, dataset_id: str, episode_index: int) -> dict[str, Any] | None:
        """Return the explicitly bound {job, target} pair for one episode, or None when none is bound."""


class JudgeStateReader(Protocol):
    async def read(self) -> dict[str, Any]:
        """Return persisted judge state without taking a write transaction."""


class ScopedJudgeEvidenceAuthority:
    """Read current principal/generation evidence and refuse stale or invalid newest results."""

    def __init__(
        self,
        store: JudgeStateReader,
        source_revision: Callable[[str, int], Awaitable[tuple[str, str]]],
        *,
        principal_scope_id: str,
    ) -> None:
        self.store = store
        self.source_revision = source_revision
        self.principal_scope_id = principal_scope_id

    async def evidence(self, dataset_id: str, episode_index: int) -> dict[str, Any] | None:
        """Return the newest validated matching result; never substitute an older result."""
        state = await self.store.read()
        generation = state["generations"].get(dataset_id, 0)
        candidates = [
            (job, target)
            for job in state["jobs"].values()
            if job["dataset_id"] == dataset_id
            and job["actor"] == self.principal_scope_id
            and job.get("result_kind", "judge") == "judge"
            and job["generation"] == generation
            for target in job["targets"]
            if target["episode_index"] == episode_index and target["status"] == "succeeded"
        ]
        if not candidates:
            return None
        job, target = max(candidates, key=lambda pair: pair[0]["run_order"])
        try:
            read_job_evidence(job, target)
        except (KeyError, TypeError, ValueError) as exc:
            raise SavedInputError("Judge evidence binding is invalid") from exc
        current = await self.source_revision(dataset_id, episode_index)
        if (target["input"]["source_id"], target["input"]["source_revision"]) != tuple(current):
            raise SavedInputError("Judge evidence source revision changed")
        return {"job": job, "target": target}


class LocalSavedInputReader:
    """Read the documented local curation layout beneath one configured dataset root."""

    def __init__(self, root: Path, *, judge_evidence: JudgeEvidenceAuthority | None = None) -> None:
        self.root = root.resolve()
        self.judge_evidence = judge_evidence

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

    def _labels(self, dataset_id: str) -> SavedResource:
        resource = self._read(Path("meta") / "episode_labels.json")
        if resource.value is not None and resource.value.get("dataset_id") != dataset_id:
            raise SavedInputError("Curation dataset identity mismatch")
        return resource

    async def release_resource(self, dataset_id: str, selection: SavedSelection) -> SavedResource:
        """Read selected labels, episode ledger, or bound judge evidence; other kinds are unavailable."""
        if selection.kind == "labels":
            return await asyncio.to_thread(self._labels, dataset_id)
        if selection.kind == "ledger":
            labels = await asyncio.to_thread(self._labels, dataset_id)
            ledger = ((labels.value or {}).get("provenance") or {}).get(str(selection.episode_index))
            if ledger is None:
                return SavedResource(None, None)
            if not isinstance(ledger, dict):
                raise SavedInputError("Invalid saved ledger")
            return SavedResource(ledger, content_etag(ledger))
        if selection.kind == "evidence":
            if self.judge_evidence is None:
                raise SavedInputError("Judge evidence authority is not bound", 503)
            bound = await self.judge_evidence.evidence(dataset_id, selection.episode_index)
            return bound_judge_evidence(bound, dataset_id, selection.episode_index)
        return SavedResource(None, None)

    async def source_revision(self, dataset_id: str, episode_index: int) -> tuple[str, str]:
        return await asyncio.to_thread(local_source_revision, self.root)
