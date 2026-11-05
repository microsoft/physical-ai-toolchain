"""Freeze explicitly saved release inputs, without judge policy or write effects.

Local admission requires a finalized/quiescent dataset declaration. Descriptor
reads and repeated content inventories detect observed drift; they do not prove
a point-in-time snapshot against an arbitrary concurrent writer.
"""

from __future__ import annotations

import asyncio
import copy
import functools
import hashlib
import os
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Executor, ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from time import monotonic
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from physical_ai_curation.evidence import read_job_evidence
from physical_ai_curation.models import (
    ContributionLedger,
    DatasetLabelsFile,
    MachineOrigin,
)
from physical_ai_curation.release.canonical import (
    canonical_json_bytes,
    normalize_release_path,
    serialize_model,
    sha256_hex,
)
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.jobs import ReleaseJobError
from physical_ai_curation.release.models import (
    ContributorProjection,
    FrozenInput,
    RecordingClaim,
    RevisionSourceObject,
    SourceObject,
    SourceObjectRecord,
)
from physical_ai_curation.release.models import (
    SavedResource as FrozenSavedResource,
)
from physical_ai_curation.release.range_reader import RevisionRangeFile
from physical_ai_curation.release.record_storage import BlobLocation
from physical_ai_curation.release.store import CHUNK_BYTES, LocalReleaseStore, blob_revision, blob_source_identity
from physical_ai_curation.saved_input import SavedInputReader, SavedResource

Checkpoint = Callable[[], None]
"""Raise to stop an in-progress read at a safe boundary; ordinary callers pass None."""


class ReleaseInputError(ReleaseVerificationError):
    """Saved confirmation or finalized source no longer matches the request."""


class StaleReleaseInputError(ReleaseInputError):
    """A selected saved revision changed or disappeared and requires a fresh preview."""


class SourceReadBoundsError(ReleaseInputError):
    """The selected source cannot fit the configured read bounds."""


def _checkpoint(checkpoint: Checkpoint | None) -> None:
    if checkpoint is not None:
        checkpoint()


class ReleaseSource(Protocol):
    """Return content-bound source objects without creating snapshots or output.

    Remote implementations must read immutable versions or apply expected
    revision conditions to each read; copied checksum metadata is insufficient.
    """

    admission: Literal["finalized_quiescent", "immutable_revision", "revision_bound"]

    def inventory(self, checkpoint: Checkpoint | None = None) -> tuple[SourceObjectRecord, ...]:
        raise NotImplementedError


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


class LocalReleaseSource:
    """Content inventory beneath a configured finalized dataset directory.

    Only the existing curation sidecars are excluded. New files anywhere else
    participate in the source identity, even when not selected for output.
    """

    admission: Literal["finalized_quiescent"] = "finalized_quiescent"

    def __init__(
        self,
        root: Path,
        *,
        finalized: bool,
        on_verified: Callable[[int], None] | None = None,
    ) -> None:
        self.root = root
        self.finalized = finalized
        self.on_verified = on_verified

    @staticmethod
    def _is_source(path: str) -> bool:
        return not path.startswith(("annotations/", "_curation/")) and path != "meta/episode_labels.json"

    def inventory(self, checkpoint: Checkpoint | None = None) -> tuple[SourceObject, ...]:
        if self.finalized is not True:
            raise ReleaseInputError("local release requires a finalized/quiescent source declaration")
        store = LocalReleaseStore(self.root)
        try:
            listed = sorted(
                (item for item in store.list_objects() if self._is_source(item.path)),
                key=lambda x: x.path,
            )
            if not listed:
                raise ReleaseInputError("source inventory is empty")
            objects = []
            for item in listed:
                _checkpoint(checkpoint)
                path = self.root / item.path
                before = path.lstat()
                digest = hashlib.sha256()
                length = 0
                for chunk in store.iter_bytes(item.path, chunk_size=CHUNK_BYTES):
                    _checkpoint(checkpoint)
                    length += len(chunk)
                    digest.update(chunk)
                after = path.lstat()
                if _stat_identity(before) != _stat_identity(after) or length != item.length:
                    raise ReleaseInputError("source changed while hashing content")
                if self.on_verified is not None:
                    self.on_verified(length)
                objects.append(
                    SourceObject(
                        path=item.path,
                        revision=sha256_hex(canonical_json_bytes(_stat_identity(after))),
                        length=length,
                        sha256=digest.hexdigest(),
                    )
                )
            ending = sorted(
                (item for item in store.list_objects() if self._is_source(item.path)),
                key=lambda x: x.path,
            )
            if ending != listed:
                raise ReleaseInputError("source inventory changed during read")
            return tuple(objects)
        except ReleaseJobError:
            raise
        except (OSError, ReleaseVerificationError) as exc:
            raise ReleaseInputError("source unavailable or changed during read") from exc


@dataclass(frozen=True)
class _BlobSourceEntry:
    path: str
    name: str
    length: int
    etag: str
    version_id: str | None


@dataclass(frozen=True)
class BlobSourceBinding:
    """Current storage identity for one frozen source object, pinned to a version and ETag."""

    source: SourceObjectRecord
    name: str
    etag: str
    version_id: str | None


def _source_listing_limit(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ReleaseInputError(f"invalid {name}") from exc
    if not 1 <= value <= maximum:
        raise ReleaseInputError(f"invalid {name}")
    return value


def _checked_metadata(size: object, etag: object, version_id: object) -> None:
    if type(size) is not int or size < 0 or not isinstance(etag, str) or not etag:
        raise ReleaseInputError("source object lacks a length or expected revision")
    if version_id is not None and (not isinstance(version_id, str) or not version_id):
        raise ReleaseInputError("source listing has invalid version metadata")


class BlobReleaseSource:
    """Stream configured container/prefix bytes with revision conditions.

    The composed container client supplies Entra credentials. This adapter does
    not create snapshots, obtain credentials, stage payloads or write blobs.
    """

    admission: Literal["revision_bound"] = "revision_bound"

    def __init__(self, container_client: Any, *, prefix: str = "") -> None:
        self._client = container_client
        self._prefix = normalize_release_path(prefix.rstrip("/")) + "/" if prefix else ""
        self._max_objects = _source_listing_limit("RELEASE_SOURCE_MAX_OBJECTS", 4096, 100000)
        self._listing_bytes = _source_listing_limit("RELEASE_SOURCE_LISTING_BYTES", 4 * 1024**2, 64 * 1024**2)
        self._listing_seconds = _source_listing_limit("RELEASE_SOURCE_LISTING_TIMEOUT_SECONDS", 30, 3600)
        self._page_size = _source_listing_limit("RELEASE_SOURCE_LISTING_PAGE_SIZE", 1000, 5000)
        self._max_pages = _source_listing_limit("RELEASE_SOURCE_LISTING_MAX_PAGES", 64, 100000)
        self._read_concurrency = _source_listing_limit("RELEASE_SOURCE_READ_CONCURRENCY", 4, 16)
        self.metadata_max_bytes = _source_listing_limit("RELEASE_SOURCE_METADATA_BYTES", 16 * 1024**2, 256 * 1024**2)
        self.metadata_max_rows = _source_listing_limit("RELEASE_SOURCE_METADATA_ROWS", 10000, 1000000)
        self._scope: tuple[str, ...] | None = None

    @property
    def container_client(self) -> Any:
        """Return the configured container client; callers must not use it to bypass revision checks."""
        return self._client

    @property
    def location(self) -> BlobLocation:
        """Return the secret-free account, container, and prefix identity of the configured source."""
        address = urlsplit(str(self._client.url))
        container = address.path.strip("/")
        if (
            address.scheme != "https"
            or not address.netloc
            or address.username is not None
            or address.password is not None
            or address.query
            or address.fragment
            or not container
            or "/" in container
        ):
            raise ReleaseInputError("source container URL must be a query-free https container address")
        try:
            return BlobLocation(f"https://{address.netloc}", container, self._prefix)
        except ValueError as exc:
            raise ReleaseInputError("source container location is invalid") from exc

    def _entries(self, checkpoint: Checkpoint | None, start: str) -> tuple[_BlobSourceEntry, ...]:
        from azure.core.exceptions import AzureError

        try:
            _checkpoint(checkpoint)
            listed = []
            observed_bytes = 0
            pages_read = 0
            deadline = monotonic() + self._listing_seconds
            pager = self._client.list_blobs(name_starts_with=start, results_per_page=self._page_size).by_page()
            while True:
                _checkpoint(checkpoint)
                if monotonic() >= deadline:
                    raise SourceReadBoundsError("source listing exceeds its count or elapsed bound")
                if pages_read >= self._max_pages:
                    if getattr(pager, "continuation_token", None):
                        raise SourceReadBoundsError("source listing exceeds its page bound")
                    break
                try:
                    page = next(pager)
                except StopIteration:
                    break
                pages_read += 1
                for item in page:
                    _checkpoint(checkpoint)
                    if len(listed) >= self._max_objects or monotonic() >= deadline:
                        raise SourceReadBoundsError("source listing exceeds its count or elapsed bound")
                    metadata = {name: getattr(item, name, None) for name in ("name", "size", "etag", "version_id")}
                    try:
                        observed_bytes += len(canonical_json_bytes(metadata))
                    except (TypeError, ValueError) as exc:
                        raise ReleaseInputError("source listing metadata is invalid") from exc
                    if observed_bytes > self._listing_bytes:
                        raise SourceReadBoundsError("source listing exceeds its metadata byte bound")
                    listed.append(item)
        except AzureError as exc:
            raise ReleaseInputError("source listing unavailable") from exc
        entries = []
        for item in listed:
            _checkpoint(checkpoint)
            name = getattr(item, "name", None)
            if not isinstance(name, str) or not name.startswith(start):
                raise ReleaseInputError("source object is outside configured prefix")
            try:
                path = normalize_release_path(name[len(self._prefix) :])
            except ReleaseVerificationError as exc:
                raise ReleaseInputError("source object path is unsafe") from exc
            if not LocalReleaseSource._is_source(path):
                continue
            size = getattr(item, "size", None)
            etag = getattr(item, "etag", None)
            version_id = getattr(item, "version_id", None)
            _checked_metadata(size, etag, version_id)
            entries.append(_BlobSourceEntry(path=path, name=name, length=size, etag=etag, version_id=version_id))
        if not entries or len({entry.path for entry in entries}) != len(entries):
            raise ReleaseInputError("source inventory is empty or contains duplicate paths")
        return tuple(sorted(entries, key=lambda entry: entry.path))

    def _download(
        self,
        name: str,
        etag: str,
        version_id: str | None,
        length: int,
        chunk_size: int,
        checkpoint: Checkpoint | None = None,
    ) -> Iterator[bytes]:
        """Yield the blob bytes in pieces of at most chunk_size, conditioned on etag and version."""
        from azure.core import MatchConditions
        from azure.core.exceptions import AzureError

        try:
            _checkpoint(checkpoint)
            blob = self._client.get_blob_client(name, version_id=version_id)
            download = blob.download_blob(
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
                max_concurrency=1,
            )
            received = 0
            for raw in download.chunks():
                _checkpoint(checkpoint)
                received += len(raw)
                if received > length:
                    raise ReleaseInputError("source object length changed during read")
                for start in range(0, len(raw), chunk_size):
                    _checkpoint(checkpoint)
                    yield raw[start : start + chunk_size]
            if received != length:
                raise ReleaseInputError("source object length changed during read")
        except AzureError as exc:
            raise ReleaseInputError("source revision unavailable or changed during read") from exc

    def inventory(self, checkpoint: Checkpoint | None = None) -> tuple[RevisionSourceObject, ...]:
        """List revision-bound source objects without reading payload bytes.

        A scoped clone reads only its selected paths by exact properties and never lists the dataset.
        """
        if self._scope is not None:
            return self._scoped_inventory(checkpoint)
        return self._stable_objects(self._prefix, checkpoint)

    def metadata_inventory(self, checkpoint: Checkpoint | None = None) -> tuple[RevisionSourceObject, ...]:
        """List only configured-prefix objects under meta/; data and media prefixes are never listed."""
        if self._scope is not None:
            raise ReleaseInputError("scoped source refuses dataset-wide metadata listing")
        return self._stable_objects(self._prefix + "meta/", checkpoint)

    def scoped(self, paths: Iterable[str]) -> BlobReleaseSource:
        """Return a clone limited to explicit dataset-relative source paths.

        A scoped source can only narrow its existing scope; expansion is refused.
        """
        if isinstance(paths, str):
            raise ReleaseInputError("source scope requires an explicit collection of paths")
        requested = tuple(paths)
        if not requested or len(requested) > self._max_objects:
            raise SourceReadBoundsError("source scope must be nonempty and within the object count bound")
        for path in requested:
            try:
                normalize_release_path(path)
            except ReleaseVerificationError as exc:
                raise ReleaseInputError("source scope path is unsafe") from exc
            if not LocalReleaseSource._is_source(path):
                raise ReleaseInputError("source scope path is not a source object")
        if len(set(requested)) != len(requested):
            raise ReleaseInputError("source scope contains duplicate paths")
        if self._scope is not None and not set(requested) <= set(self._scope):
            raise ReleaseInputError("source scope expansion is refused")
        clone = copy.copy(self)
        clone._scope = tuple(sorted(requested))
        return clone

    def _stable_objects(self, start: str, checkpoint: Checkpoint | None) -> tuple[RevisionSourceObject, ...]:
        before = self._entries(checkpoint, start)
        objects = tuple(
            RevisionSourceObject(
                path=entry.path,
                length=entry.length,
                revision=blob_revision(entry.etag, entry.version_id),
                storage_identity=blob_source_identity(self.container_client.url, entry.name),
            )
            for entry in before
        )
        if self._entries(checkpoint, start) != before:
            raise ReleaseInputError("source object inventory changed during read")
        return objects

    def _scoped_inventory(self, checkpoint: Checkpoint | None) -> tuple[RevisionSourceObject, ...]:
        before = self._scoped_reads(checkpoint)
        if self._scoped_reads(checkpoint) != before:
            raise ReleaseInputError("source object inventory changed during read")
        return before

    def _scoped_reads(self, checkpoint: Checkpoint | None) -> tuple[RevisionSourceObject, ...]:
        from azure.core.exceptions import AzureError

        objects = []
        observed_bytes = 0
        deadline = monotonic() + self._listing_seconds

        def read(path: str) -> tuple[RevisionSourceObject, int]:
            _checkpoint(checkpoint)
            if monotonic() >= deadline:
                raise SourceReadBoundsError("scoped read exceeds its elapsed bound")
            name = self._prefix + path
            try:
                properties = self._client.get_blob_client(name).get_blob_properties()
            except AzureError as exc:
                raise ReleaseInputError("source object is missing or unavailable") from exc
            _checkpoint(checkpoint)
            if monotonic() >= deadline:
                raise SourceReadBoundsError("scoped read exceeds its elapsed bound")
            size = getattr(properties, "size", None)
            etag = getattr(properties, "etag", None)
            version_id = getattr(properties, "version_id", None)
            _checked_metadata(size, etag, version_id)
            metadata = {"name": name, "size": size, "etag": etag, "version_id": version_id}
            return (
                RevisionSourceObject(
                    path=path,
                    length=size,
                    revision=blob_revision(etag, version_id),
                    storage_identity=blob_source_identity(self.container_client.url, name),
                ),
                len(canonical_json_bytes(metadata)),
            )

        executor = ThreadPoolExecutor(max_workers=self._read_concurrency)
        try:
            futures = [executor.submit(copy_context().run, read, path) for path in self._scope or ()]
            for future in futures:
                item, size = future.result()
                _checkpoint(checkpoint)
                if monotonic() >= deadline:
                    raise SourceReadBoundsError("scoped read exceeds its elapsed bound")
                observed_bytes += size
                if observed_bytes > self._listing_bytes:
                    raise SourceReadBoundsError("scoped read exceeds its metadata byte bound")
                objects.append(item)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
        return tuple(objects)

    def binding(self, source: SourceObjectRecord, *, checkpoint: Checkpoint | None = None) -> BlobSourceBinding:
        """Return the current storage binding for source, refusing before any download."""
        from azure.core.exceptions import AzureError

        name = self._prefix + normalize_release_path(source.path)
        if self._scope is not None and source.path not in self._scope:
            raise ReleaseInputError("source object is outside the scoped read")
        if isinstance(source, RevisionSourceObject) and source.storage_identity != blob_source_identity(
            self.container_client.url, name
        ):
            raise ReleaseInputError("source identity changed")
        _checkpoint(checkpoint)
        try:
            properties = self._client.get_blob_client(name).get_blob_properties()
        except AzureError as exc:
            raise ReleaseInputError("source object is missing or unavailable") from exc
        _checkpoint(checkpoint)
        if properties.size != source.length:
            raise ReleaseInputError("source object length changed")
        version_id = getattr(properties, "version_id", None)
        if blob_revision(properties.etag, version_id) != source.revision:
            raise ReleaseInputError("source object revision changed")
        return BlobSourceBinding(source=source, name=name, etag=properties.etag, version_id=version_id)

    def range_file(self, source: SourceObjectRecord, *, checkpoint: Checkpoint | None = None) -> RevisionRangeFile:
        """Return a seekable view whose every range is conditioned on the revision bound before reading."""
        from azure.core import MatchConditions
        from azure.core.exceptions import AzureError

        if not isinstance(source, RevisionSourceObject):
            raise ReleaseInputError("range reads require revision-bound source objects")
        binding = self.binding(source, checkpoint=checkpoint)
        blob = self._client.get_blob_client(binding.name, version_id=binding.version_id)
        deadline = monotonic() + self._listing_seconds

        def fetch(offset: int, length: int) -> bytes:
            buffer = bytearray()
            _checkpoint(checkpoint)
            if monotonic() >= deadline:
                raise ReleaseInputError("source range read exceeded its deadline")
            try:
                download = blob.download_blob(
                    offset=offset,
                    length=length,
                    etag=binding.etag,
                    match_condition=MatchConditions.IfNotModified,
                    max_concurrency=1,
                )
                for chunk in download.chunks():
                    _checkpoint(checkpoint)
                    if monotonic() >= deadline:
                        raise ReleaseInputError("source range read exceeded its deadline")
                    if len(buffer) + len(chunk) > length:
                        raise ReleaseInputError("source range read returned more bytes than requested")
                    buffer.extend(chunk)
            except AzureError as exc:
                raise ReleaseInputError("source revision unavailable or changed during range read") from exc
            _checkpoint(checkpoint)
            if monotonic() >= deadline:
                raise ReleaseInputError("source range read exceeded its deadline")
            return bytes(buffer)

        return RevisionRangeFile(source.length, fetch)

    def iter_bytes(
        self,
        source: SourceObjectRecord,
        *,
        chunk_size: int = CHUNK_BYTES,
        checkpoint: Checkpoint | None = None,
    ) -> Iterator[bytes]:
        """Return source bytes in pieces of at most chunk_size, conditioned on the listed revision.

        The binding is resolved eagerly, so a stale source refuses before any download.
        Digest-bound objects are compared after the final chunk; revision-bound objects rely on the
        conditional read. Consume the iterator fully before trusting the bytes.
        """
        if type(chunk_size) is not int or chunk_size <= 0:
            raise ReleaseInputError("chunk_size must be a positive integer")
        return self._verified_stream(self.binding(source, checkpoint=checkpoint), chunk_size, checkpoint)

    def _verified_stream(
        self,
        binding: BlobSourceBinding,
        chunk_size: int,
        checkpoint: Checkpoint | None = None,
    ) -> Iterator[bytes]:
        source = binding.source
        if not isinstance(source, SourceObject):
            yield from self._download(
                binding.name,
                binding.etag,
                binding.version_id,
                source.length,
                chunk_size,
                checkpoint,
            )
            return
        digest = hashlib.sha256()
        for chunk in self._download(
            binding.name,
            binding.etag,
            binding.version_id,
            source.length,
            chunk_size,
            checkpoint,
        ):
            digest.update(chunk)
            yield chunk
        if digest.hexdigest() != source.sha256:
            raise ReleaseInputError("source object content changed during read")


@dataclass(frozen=True)
class SavedSelection:
    """One explicitly selected persisted resource and its expected revision.

    Extended resources use the reader's release_resource method; judge evidence
    must provide the persisted job/target pair for independent binding checks.
    """

    kind: Literal[
        "annotation",
        "edits",
        "labels",
        "ledger",
        "evidence",
        "recording_receipt",
        "import_mapping",
    ]
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
    reader: SavedInputReader,
    selection: ReleaseSelection,
    item: SavedSelection,
    context: FreezeContext,
    *,
    verify_revision: bool = True,
) -> SavedResource:
    if item.kind == "annotation":
        value = await reader.annotation(selection.dataset_id, item.episode_index)
    elif item.kind == "edits":
        value = await reader.edits(
            selection.dataset_id,
            item.episode_index,
            context.principal_scope_id,
            context.source,
        )
    else:
        extended = getattr(reader, "release_resource", None)
        if extended is None:
            raise ReleaseInputError("selected saved resource is not supported by this reader")
        value = await extended(selection.dataset_id, item)
    if value.value is None or not value.etag or (verify_revision and value.etag != item.expected_revision):
        raise StaleReleaseInputError("selected saved resource revision is stale or unavailable")
    return value


async def capture_saved_selection(
    reader: SavedInputReader,
    selection: ReleaseSelection,
    context: FreezeContext,
    checkpoint: Checkpoint | None = None,
) -> ReleaseSelection:
    """Resolve current revisions of explicitly selected saved resources at execution time."""
    resources = []
    for item in selection.resources:
        _checkpoint(checkpoint)
        saved = await _read_saved(reader, selection, item, context, verify_revision=False)
        resources.append(replace(item, expected_revision=saved.etag))
    return replace(selection, resources=tuple(resources))


def _portable_author(author: str, labels: dict[str, str]) -> str:
    """Assign a release-local alias without exposing private identity or merging authors."""
    if not isinstance(author, str) or not author:
        raise ReleaseInputError("saved contributor identity must be a nonempty string")
    if author not in labels:
        number = len(labels) + 1
        alias = f"contributor-{number}"
        while alias in labels.values():
            number += 1
            alias = f"contributor-{number}"
        labels[author] = alias
    return labels[author]


def _portable_value(value: Any, labels: dict[str, str]) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, child in sorted(value.items()):
            if key in {
                "principal_scope_id",
                "source_id",
                "source_revision",
                "endpoint",
                "api_key",
                "token",
            }:
                continue
            if key in {"author_id", "annotator_id"} and child is not None:
                result[key] = _portable_author(child, labels)
            elif key == "acceptances":
                result[key] = {
                    identity: [_portable_author(author, labels) for author in authors]
                    for identity, authors in sorted(child.items())
                }
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


def _selected_labels(value: dict[str, Any], episode_index: int) -> dict[str, Any]:
    """Keep dataset label metadata and only the selected episode's labels, analysis, and provenance."""
    key = str(episode_index)
    selected = {
        field: {key: value[field][key]}
        for field in ("episodes", "analysis", "provenance")
        if key in value.get(field, {})
    }
    return {field: value[field] for field in ("dataset_id", "available_labels") if field in value} | selected


def _project_saved(value: dict[str, Any], item: SavedSelection, labels: dict[str, str]) -> dict[str, Any]:
    if item.kind == "labels":
        DatasetLabelsFile.model_validate(value)
        value = _selected_labels(value, item.episode_index)
    elif item.kind == "annotation":
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


async def _join_owned(future: asyncio.Future[Any]) -> None:
    """Wait for an owned worker thread to finish, absorbing repeated task cancellation until it settles."""
    while not future.done():
        try:
            await asyncio.wait({future})
        except asyncio.CancelledError:
            continue
    if not future.cancelled():
        future.exception()


async def _settled_inventory(
    source: ReleaseSource,
    checkpoint: Checkpoint | None = None,
    executor: Executor | None = None,
) -> tuple[SourceObjectRecord, ...]:
    """Run a blocking inventory; on cancellation, join its thread before propagating."""
    future = asyncio.get_running_loop().run_in_executor(
        executor, functools.partial(source.inventory, checkpoint=checkpoint)
    )
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        await _join_owned(future)
        raise


async def freeze_release_inputs(
    source: ReleaseSource,
    reader: SavedInputReader,
    selection: ReleaseSelection,
    context: FreezeContext,
    checkpoint: Checkpoint | None = None,
    *,
    executor: Executor | None = None,
) -> FrozenReleaseInputs:
    """Read and retain exact selected saved values; perform no acceptance or writes."""
    await _check_context(reader, selection, context)
    _checkpoint(checkpoint)
    before = await _settled_inventory(source, checkpoint, executor)
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
        _checkpoint(checkpoint)
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
        resources.append(
            FrozenSavedResource(
                resource_id=resource_id,
                revision=saved.etag,
                value_json=content.decode(),
            )
        )
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
        _checkpoint(checkpoint)
        current = await _read_saved(reader, selection, item, context)
        if canonical_json_bytes(current.value) != original:
            raise ReleaseInputError("selected saved values changed during freeze")
    _checkpoint(checkpoint)
    after = await _settled_inventory(source, checkpoint, executor)
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
    checkpoint: Checkpoint | None = None,
) -> FrozenReleaseInputs:
    """Reject stale pre-acceptance confirmation; never rewrite accepted content."""
    if sha256_hex(canonical_json_bytes(asdict(context))) != expected.context_sha256:
        raise ReleaseInputError("release source/principal context changed")
    if sha256_hex(canonical_json_bytes(asdict(selection))) != expected.request_sha256:
        raise ReleaseInputError("release selection changed")
    current = await freeze_release_inputs(source, reader, selection, context, checkpoint)
    if current.sha256 != expected.sha256:
        raise ReleaseInputError("release source or saved inputs changed; preview again")
    return expected
