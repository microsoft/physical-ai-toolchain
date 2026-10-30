"""Freeze explicitly saved release inputs, without judge policy or write effects.

Local admission requires a finalized/quiescent dataset declaration. Descriptor
reads and repeated content inventories detect observed drift; they do not prove
a point-in-time snapshot against an arbitrary concurrent writer.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from physical_ai_curation.evidence import read_job_evidence
from physical_ai_curation.models import ContributionLedger, MachineOrigin
from physical_ai_curation.release.canonical import (
    canonical_json_bytes,
    normalize_release_path,
    serialize_model,
    sha256_hex,
)
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.models import (
    ContributorProjection,
    FrozenInput,
    RecordingClaim,
    SourceObject,
)
from physical_ai_curation.release.models import (
    SavedResource as FrozenSavedResource,
)
from physical_ai_curation.release.store import CHUNK_BYTES, LocalReleaseStore
from physical_ai_curation.saved_input import SavedInputReader, SavedResource


class ReleaseInputError(ReleaseVerificationError):
    """Saved confirmation or finalized source no longer matches the request."""


class ReleaseSource(Protocol):
    """Return content-bound source objects without creating snapshots or output.

    Remote implementations must read immutable versions or apply expected
    revision conditions to each read; copied checksum metadata is insufficient.
    """

    admission: Literal["finalized_quiescent", "immutable_revision", "revision_bound"]

    def inventory(self) -> tuple[SourceObject, ...]: ...


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


class LocalReleaseSource:
    """Content inventory beneath a configured finalized dataset directory.

    Only the existing curation sidecars are excluded. New files anywhere else
    participate in the source identity, even when not selected for output.
    """

    admission: Literal["finalized_quiescent"] = "finalized_quiescent"

    def __init__(self, root: Path, *, finalized: bool) -> None:
        self.root = root
        self.finalized = finalized

    @staticmethod
    def _is_source(path: str) -> bool:
        return not path.startswith(("annotations/", "_curation/")) and path != "meta/episode_labels.json"

    def inventory(self) -> tuple[SourceObject, ...]:
        if self.finalized is not True:
            raise ReleaseInputError("local release requires a finalized/quiescent source declaration")
        store = LocalReleaseStore(self.root)
        try:
            listed = sorted((item for item in store.list_objects() if self._is_source(item.path)), key=lambda x: x.path)
            if not listed:
                raise ReleaseInputError("source inventory is empty")
            objects = []
            for item in listed:
                path = self.root / item.path
                before = path.lstat()
                digest = hashlib.sha256()
                length = 0
                for chunk in store.iter_bytes(item.path, chunk_size=CHUNK_BYTES):
                    length += len(chunk)
                    digest.update(chunk)
                after = path.lstat()
                if _stat_identity(before) != _stat_identity(after) or length != item.length:
                    raise ReleaseInputError("source changed while hashing content")
                objects.append(
                    SourceObject(
                        path=item.path,
                        revision=sha256_hex(canonical_json_bytes(_stat_identity(after))),
                        length=length,
                        sha256=digest.hexdigest(),
                    )
                )
            ending = sorted((item for item in store.list_objects() if self._is_source(item.path)), key=lambda x: x.path)
            if ending != listed:
                raise ReleaseInputError("source inventory changed during read")
            return tuple(objects)
        except (OSError, ReleaseVerificationError) as exc:
            raise ReleaseInputError("source unavailable or changed during read") from exc


@dataclass(frozen=True)
class _BlobSourceEntry:
    path: str
    name: str
    length: int
    etag: str
    version_id: str | None


class BlobReleaseSource:
    """Stream configured container/prefix bytes with revision conditions.

    The composed container client supplies Entra credentials. This adapter does
    not create snapshots, obtain credentials, stage payloads or write blobs.
    """

    admission: Literal["revision_bound"] = "revision_bound"

    def __init__(self, container_client: Any, *, prefix: str = "") -> None:
        self._client = container_client
        self._prefix = normalize_release_path(prefix.rstrip("/")) + "/" if prefix else ""

    def _entries(self) -> tuple[_BlobSourceEntry, ...]:
        entries = []
        for item in self._client.list_blobs(name_starts_with=self._prefix):
            if not item.name.startswith(self._prefix):
                raise ReleaseInputError("source object is outside configured prefix")
            path = normalize_release_path(item.name[len(self._prefix) :])
            if not LocalReleaseSource._is_source(path):
                continue
            if type(item.size) is not int or item.size < 0 or not isinstance(item.etag, str) or not item.etag:
                raise ReleaseInputError("source listing lacks a length or expected revision")
            entries.append(
                _BlobSourceEntry(
                    path=path,
                    name=item.name,
                    length=item.size,
                    etag=item.etag,
                    version_id=getattr(item, "version_id", None),
                )
            )
        if not entries or len({entry.path for entry in entries}) != len(entries):
            raise ReleaseInputError("source inventory is empty or contains duplicate paths")
        return tuple(sorted(entries, key=lambda entry: entry.path))

    def inventory(self) -> tuple[SourceObject, ...]:
        from azure.core import MatchConditions
        from azure.core.exceptions import AzureError

        try:
            before = self._entries()
            objects = []
            for entry in before:
                blob = self._client.get_blob_client(entry.name, version_id=entry.version_id)
                download = blob.download_blob(
                    etag=entry.etag, match_condition=MatchConditions.IfNotModified, max_concurrency=1
                )
                digest = hashlib.sha256()
                length = 0
                for chunk in download.chunks():
                    length += len(chunk)
                    if length > entry.length:
                        raise ReleaseInputError("source object length changed during read")
                    digest.update(chunk)
                if length != entry.length:
                    raise ReleaseInputError("source object length changed during read")
                objects.append(
                    SourceObject(
                        path=entry.path,
                        length=length,
                        sha256=digest.hexdigest(),
                        revision=sha256_hex(canonical_json_bytes({"etag": entry.etag, "version_id": entry.version_id})),
                    )
                )
            if self._entries() != before:
                raise ReleaseInputError("source object inventory changed during read")
            return tuple(objects)
        except AzureError as exc:
            raise ReleaseInputError("source revision unavailable or changed during read") from exc


@dataclass(frozen=True)
class SavedSelection:
    """One explicitly selected persisted resource and its expected revision.

    Extended resources use the reader's release_resource method; judge evidence
    must provide the persisted job/target pair for independent binding checks.
    """

    kind: Literal["annotation", "edits", "labels", "ledger", "evidence", "recording_receipt", "import_mapping"]
    episode_index: int
    expected_revision: str
    annotation_author_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {
            "annotation",
            "edits",
            "labels",
            "ledger",
            "evidence",
            "recording_receipt",
            "import_mapping",
        }:
            raise ReleaseInputError("unsupported saved resource kind")
        if type(self.episode_index) is not int or self.episode_index < 0 or not self.expected_revision:
            raise ReleaseInputError("saved resources require an episode and explicit revision")
        if self.annotation_author_id is not None and (not self.annotation_author_id or self.kind != "annotation"):
            raise ReleaseInputError("annotation authors apply only to selected annotations")


@dataclass(frozen=True)
class ReleaseSelection:
    """Explicit episode order and saved resources; drafts have no input field."""

    dataset_id: str
    episode_indices: tuple[int, ...]
    resources: tuple[SavedSelection, ...] = ()

    def __post_init__(self) -> None:
        if not self.dataset_id or not self.episode_indices:
            raise ReleaseInputError("select an explicit dataset and episode selection")
        if any(type(index) is not int or index < 0 for index in self.episode_indices):
            raise ReleaseInputError("episode indices must be nonnegative integers")
        if len(set(self.episode_indices)) != len(self.episode_indices):
            raise ReleaseInputError("episode selection contains duplicates")
        keys = [(item.kind, item.episode_index, item.annotation_author_id) for item in self.resources]
        if len(set(keys)) != len(keys) or any(
            item.episode_index not in self.episode_indices for item in self.resources
        ):
            raise ReleaseInputError("saved resources require unique explicitly selected episodes")


@dataclass(frozen=True)
class FreezeContext:
    """Private request context; never serialize this object into a release.

    Public contributor labels come from configured authority, not actor
    overrides. Authentication and grants belong to the service boundary.
    """

    principal_scope_id: str
    source: tuple[str, str]
    contributor_labels: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.principal_scope_id == "localuser" and not self.contributor_labels:
            object.__setattr__(self, "contributor_labels", (("localuser", "localuser"),))
        if len({private for private, _ in self.contributor_labels}) != len(self.contributor_labels):
            raise ReleaseInputError("contributor bindings must be unique")
        if len({label for _, label in self.contributor_labels}) != len(self.contributor_labels):
            raise ReleaseInputError("portable contributor labels must be distinct")
        if any(not private or not label for private, label in self.contributor_labels):
            raise ReleaseInputError("contributor bindings must be nonempty")


@dataclass(frozen=True)
class FrozenReleaseInputs:
    """Portable immutable content plus separate private confirmation bindings."""

    content: FrozenInput
    context_sha256: str
    request_sha256: str

    @property
    def sha256(self) -> str:
        return sha256_hex(serialize_model(self.content))


async def _check_context(reader: SavedInputReader, selection: ReleaseSelection, context: FreezeContext) -> None:
    if not context.principal_scope_id or not all(context.source):
        raise ReleaseInputError("release source/principal context is required")
    for index in selection.episode_indices:
        if await reader.source_revision(selection.dataset_id, index) != context.source:
            raise ReleaseInputError("release source context changed")


async def _read_saved(
    reader: SavedInputReader, selection: ReleaseSelection, item: SavedSelection, context: FreezeContext
) -> SavedResource:
    if item.kind == "annotation":
        value = await reader.annotation(selection.dataset_id, item.episode_index)
    elif item.kind == "edits":
        value = await reader.edits(selection.dataset_id, item.episode_index, context.principal_scope_id, context.source)
    else:
        extended = getattr(reader, "release_resource", None)
        if extended is None:
            raise ReleaseInputError("selected saved resource is not supported by this reader")
        value = await extended(selection.dataset_id, item)
    if not item.expected_revision or value.etag != item.expected_revision or value.value is None:
        raise ReleaseInputError("selected saved resource revision is stale or unavailable")
    return value


def _portable_value(value: Any, labels: dict[str, str]) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if key in {"principal_scope_id", "source_id", "source_revision", "endpoint", "api_key", "token"}:
                continue
            if key in {"author_id", "annotator_id"} and child is not None:
                if child not in labels:
                    raise ReleaseInputError("selected authors require configured portable contributor labels")
                result[key] = labels[child]
            elif key == "acceptances":
                try:
                    result[key] = {
                        identity: [labels[author] for author in authors] for identity, authors in child.items()
                    }
                except KeyError as exc:
                    raise ReleaseInputError("selected acceptances require portable contributor labels") from exc
            elif key == "machine" and child is not None:
                result[key] = MachineOrigin.model_validate(child).model_dump(mode="json")
            else:
                result[key] = _portable_value(child, labels)
        return result
    if isinstance(value, list):
        return [_portable_value(child, labels) for child in value]
    return value


def _referenced_authors(value: Any) -> set[str]:
    authors = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"author_id", "annotator_id"} and isinstance(child, str):
                authors.add(child)
            elif key == "acceptances":
                authors.update(author for accepted in child.values() for author in accepted)
            else:
                authors.update(_referenced_authors(child))
    elif isinstance(value, list):
        for child in value:
            authors.update(_referenced_authors(child))
    return authors


def _project_saved(value: dict[str, Any], item: SavedSelection, labels: dict[str, str]) -> dict[str, Any]:
    if item.kind == "annotation":
        authors = value.get("annotations", [])
        if item.annotation_author_id is not None:
            authors = [entry for entry in authors if entry.get("annotator_id") == item.annotation_author_id]
            if len(authors) != 1:
                raise ReleaseInputError("selected annotation author is unavailable or ambiguous")
        elif len(authors) > 1:
            raise ReleaseInputError("select an explicit saved annotation author")
        value = {"annotations": authors}
    elif item.kind == "ledger":
        value = ContributionLedger.model_validate(value).model_dump(mode="json")
    elif item.kind == "evidence":
        record = read_job_evidence(value["job"], value["target"])
        value = {
            "schema_version": record.schema_version,
            "kind": record.kind,
            "payload": record.payload,
            "invocation_id": record.invocation_id,
            "input_id": record.input_id,
            "declared_identity": {key: record.declared_identity[key] for key in ("model_id", "prompt_version")},
        }
    return _portable_value(value, labels)


async def freeze_release_inputs(
    source: ReleaseSource,
    reader: SavedInputReader,
    selection: ReleaseSelection,
    context: FreezeContext,
) -> FrozenReleaseInputs:
    """Read and retain exact selected saved values; perform no acceptance or writes."""
    await _check_context(reader, selection, context)
    before = await asyncio.to_thread(source.inventory)
    labels = dict(context.contributor_labels)
    resources = []
    selected_authors = set()
    identities = []
    evidence = []
    claims = {
        index: RecordingClaim(episode_id=f"{selection.dataset_id}/episode_{index:06d}", origin="unknown")
        for index in selection.episode_indices
    }
    original_values = []
    for item in selection.resources:
        saved = await _read_saved(reader, selection, item, context)
        original_values.append(canonical_json_bytes(saved.value))
        try:
            projected = _project_saved(saved.value, item, labels)
        except (ValueError, KeyError, TypeError) as exc:
            raise ReleaseInputError("selected saved resource has invalid portable evidence") from exc
        selected_authors.update(_referenced_authors(projected))
        content = canonical_json_bytes(projected)
        suffix = labels.get(item.annotation_author_id, "") if item.annotation_author_id else ""
        resource_id = f"{item.kind}/episode_{item.episode_index:06d}" + (f"/{suffix}" if suffix else "")
        resources.append(FrozenSavedResource(resource_id=resource_id, revision=saved.etag, value_json=content.decode()))
        if item.kind == "ledger":
            identities.extend(entry["id"] for entry in projected["contributions"])
        elif item.kind == "evidence":
            evidence.append(sha256_hex(content))
        elif item.kind == "recording_receipt":
            claims[item.episode_index] = RecordingClaim(
                episode_id=f"{selection.dataset_id}/episode_{item.episode_index:06d}",
                origin="supplied_receipt",
                receipt_sha256=sha256_hex(content),
            )
    for item, original in zip(selection.resources, original_values, strict=True):
        current = await _read_saved(reader, selection, item, context)
        if canonical_json_bytes(current.value) != original:
            raise ReleaseInputError("selected saved values changed during freeze")
    after = await asyncio.to_thread(source.inventory)
    if before != after:
        raise ReleaseInputError("source content or object inventory changed during freeze")
    await _check_context(reader, selection, context)
    contributors = [ContributorProjection(contributor_label=label, role="author") for label in sorted(selected_authors)]
    if context.principal_scope_id in labels:
        contributors.append(
            ContributorProjection(contributor_label=labels[context.principal_scope_id], role="initiator")
        )
    content = FrozenInput(
        selected_episode_ids=tuple(
            f"{selection.dataset_id}/episode_{index:06d}" for index in selection.episode_indices
        ),
        source_admission=source.admission,
        source_objects=before,
        saved_resources=tuple(resources),
        ledger_contribution_ids=tuple(dict.fromkeys(identities)),
        evidence_references=tuple(evidence),
        contributors=tuple(contributors),
        recording_claims=tuple(claims[index] for index in selection.episode_indices),
    )
    return FrozenReleaseInputs(
        content=content,
        context_sha256=sha256_hex(canonical_json_bytes(asdict(context))),
        request_sha256=sha256_hex(canonical_json_bytes(asdict(selection))),
    )


async def revalidate_release_inputs(
    expected: FrozenReleaseInputs,
    source: ReleaseSource,
    reader: SavedInputReader,
    selection: ReleaseSelection,
    context: FreezeContext,
) -> FrozenReleaseInputs:
    """Reject stale pre-acceptance confirmation; never rewrite accepted content."""
    if sha256_hex(canonical_json_bytes(asdict(context))) != expected.context_sha256:
        raise ReleaseInputError("release source/principal context changed")
    if sha256_hex(canonical_json_bytes(asdict(selection))) != expected.request_sha256:
        raise ReleaseInputError("release selection changed")
    current = await freeze_release_inputs(source, reader, selection, context)
    if current.sha256 != expected.sha256:
        raise ReleaseInputError("release source or saved inputs changed; preview again")
    return expected
