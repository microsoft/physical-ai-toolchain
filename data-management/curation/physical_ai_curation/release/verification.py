"""Candidate and publication verification for release packages.

Candidate verification checks the closed object set without a marker. Public
verification additionally requires the final marker and bindings to the
manifest, inventory, receipt, and checksums bytes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from physical_ai_curation.release.canonical import (
    normalize_release_path,
    parse_strict_json,
    serialize_model,
    sha256_hex,
)
from physical_ai_curation.release.errors import IncompleteReleaseError, ReleaseVerificationError
from physical_ai_curation.release.models import (
    CANDIDATE_ASSURANCE,
    CANDIDATE_CONTROL_PATHS,
    CHECKSUMS_PATH,
    CONTROL_PATHS,
    INVENTORY_PATH,
    MANIFEST_PATH,
    MARKER_PATH,
    RECEIPT_PATH,
    VERIFIER_VERSION,
    FrameLineageRecord,
    FrozenInput,
    Inventory,
    InventoryEntry,
    InventoryObject,
    LineageStream,
    PackageIdentity,
    ReleaseManifest,
    ReleaseMarker,
    ReleasePreview,
    ReleaseResult,
    RevisionSourceObject,
    StorageRevisionEntry,
    UploadedContentEntry,
    VerificationReceipt,
)
from physical_ai_curation.release.store import CHUNK_BYTES, ObjectStat, ReleaseStore

MAX_CONTROL_BYTES = 1024 * 1024
MAX_LINEAGE_RECORD_BYTES = 64 * 1024
ContentEntry = InventoryEntry | UploadedContentEntry

RECEIPT_CHECKS = (
    "inventory_closure",
    "content_digests",
    "storage_revision_bindings",
    "uploaded_content_bindings",
    "provenance_mappings",
    "frozen_input_binding",
    "preview_binding",
    "episode_mappings",
    "canonical_control_bytes",
    "producer_identity",
    "lineage_stream_binding",
)


@dataclass(frozen=True)
class _Package:
    manifest: ReleaseManifest
    manifest_bytes: bytes
    inventory: Inventory
    inventory_bytes: bytes
    contents: dict[str, bytes]
    present: frozenset[str]
    objects: tuple[ObjectStat, ...]
    episode_mapping_count: int
    frame_mapping_count: int


@dataclass(frozen=True)
class CandidateVerification:
    """Outcome of candidate verification plus the bytes of derived control artifacts."""

    result: ReleaseResult
    manifest_sha256: str
    inventory_sha256: str
    receipt_bytes: bytes
    checksums_bytes: bytes


def verify_candidate(
    store: ReleaseStore,
    expected_manifest_sha256: str | None = None,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> CandidateVerification:
    """Verify a candidate package; a final marker is rejected."""
    package = _load(store, CANDIDATE_CONTROL_PATHS, checkpoint)
    if expected_manifest_sha256 is not None and sha256_hex(package.manifest_bytes) != expected_manifest_sha256:
        raise ReleaseVerificationError("manifest digest does not match expected value")
    candidate = _build_candidate(package)
    for path, expected in ((RECEIPT_PATH, candidate.receipt_bytes), (CHECKSUMS_PATH, candidate.checksums_bytes)):
        _require_matching_control(store, package, path, expected)
    _require_unchanged_listing(store, package)
    return candidate


@dataclass(frozen=True)
class VerifiedPublication:
    """Published result with the marker and receipt read during the same verification pass."""

    result: ReleaseResult
    marker: ReleaseMarker
    receipt: VerificationReceipt


def verify_published(
    store: ReleaseStore,
    expected_manifest_sha256: str | None = None,
    *,
    checkpoint: Callable[[], None] | None = None,
    expected_release_name: str | None = None,
    expected_source_dataset_id: str | None = None,
) -> ReleaseResult:
    """Verify a finalized package and return a published result bound to its identity."""
    return verify_published_publication(
        store,
        expected_manifest_sha256,
        checkpoint=checkpoint,
        expected_release_name=expected_release_name,
        expected_source_dataset_id=expected_source_dataset_id,
    ).result


def verify_published_publication(
    store: ReleaseStore,
    expected_manifest_sha256: str | None = None,
    *,
    checkpoint: Callable[[], None] | None = None,
    expected_release_name: str | None = None,
    expected_source_dataset_id: str | None = None,
) -> VerifiedPublication:
    """Verify a finalized package and return the result with its verified marker and receipt."""
    package = _load(store, CONTROL_PATHS, checkpoint)
    if expected_manifest_sha256 is not None and sha256_hex(package.manifest_bytes) != expected_manifest_sha256:
        raise ReleaseVerificationError("manifest digest does not match expected value")
    missing = sorted({MARKER_PATH, RECEIPT_PATH, CHECKSUMS_PATH} - package.present)
    if missing:
        raise IncompleteReleaseError(f"release is not finalized; missing: {', '.join(missing)}")
    candidate = _build_candidate(package)
    for path, expected in ((RECEIPT_PATH, candidate.receipt_bytes), (CHECKSUMS_PATH, candidate.checksums_bytes)):
        _require_matching_control(store, package, path, expected)
    marker_bytes = store.read_control_bytes(MARKER_PATH, max_bytes=MAX_CONTROL_BYTES)
    marker = _parse_model(ReleaseMarker, marker_bytes, "marker")
    _require_canonical(marker_bytes, marker, "marker")
    if marker != _expected_marker(candidate, marker.release_name, marker.source_dataset_id):
        raise ReleaseVerificationError("marker bindings do not match release contents")
    if expected_release_name is not None and marker.release_name != expected_release_name:
        raise ReleaseVerificationError("published release identity does not match expected release name")
    if expected_source_dataset_id is not None and marker.source_dataset_id != expected_source_dataset_id:
        raise ReleaseVerificationError("published release identity does not match expected source dataset")
    _require_unchanged_listing(store, package)
    result = ReleaseResult(
        identity=candidate.result.identity.model_copy(
            update={"release_name": marker.release_name, "source_dataset_id": marker.source_dataset_id}
        ),
        publication_state="published",
        checksum_covered_count=marker.checksum_covered_count,
        checksum_omitted_count=marker.checksum_omitted_count,
    )
    receipt = _parse_model(VerificationReceipt, candidate.receipt_bytes, "receipt")
    return VerifiedPublication(result=result, marker=marker, receipt=receipt)


def build_marker(candidate: CandidateVerification, *, release_name: str, source_dataset_id: str) -> bytes:
    """Return canonical marker bytes binding the verified candidate artifacts and identity."""
    return serialize_model(_expected_marker(candidate, release_name, source_dataset_id))


def _expected_marker(candidate: CandidateVerification, release_name: str, source_dataset_id: str) -> ReleaseMarker:
    identity = candidate.result.identity
    receipt = _parse_model(VerificationReceipt, candidate.receipt_bytes, "receipt")
    return ReleaseMarker(
        package_id=identity.package_id,
        release_name=release_name,
        source_dataset_id=source_dataset_id,
        manifest_sha256=identity.manifest_sha256,
        inventory_sha256=candidate.inventory_sha256,
        receipt_sha256=sha256_hex(candidate.receipt_bytes),
        checksums_sha256=sha256_hex(candidate.checksums_bytes),
        checksum_covered_count=receipt.checksum_covered_count,
        checksum_omitted_count=receipt.checksum_omitted_count,
    )


def _list_stable(store: ReleaseStore) -> list[ObjectStat]:
    """List twice and fail if the closed object set changed between listings."""
    first = sorted(store.list_objects(), key=lambda item: item.path)
    second = sorted(store.list_objects(), key=lambda item: item.path)
    if first != second:
        raise ReleaseVerificationError("release object set changed during verification")
    for item in first:
        normalize_release_path(item.path)
    return first


def _load(store: ReleaseStore, control_paths: frozenset[str], checkpoint: Callable[[], None] | None = None) -> _Package:
    listed = _list_stable(store)
    index = {item.path: item for item in listed}
    if len(index) != len(listed):
        raise ReleaseVerificationError("duplicate release object path")
    for required in (MANIFEST_PATH, INVENTORY_PATH):
        if required not in index:
            raise ReleaseVerificationError(f"missing control artifact: {required}")

    manifest_bytes = store.read_control_bytes(MANIFEST_PATH, max_bytes=MAX_CONTROL_BYTES)
    manifest = _parse_model(ReleaseManifest, manifest_bytes, "manifest")
    _require_canonical(manifest_bytes, manifest, "manifest")

    inventory_bytes = store.read_control_bytes(INVENTORY_PATH, max_bytes=MAX_CONTROL_BYTES)
    if sha256_hex(inventory_bytes) != manifest.inventory_sha256:
        raise ReleaseVerificationError("inventory digest does not match manifest binding")
    inventory = _parse_model(Inventory, inventory_bytes, "inventory")
    _require_canonical(inventory_bytes, inventory, "inventory")

    declared = {entry.path for entry in inventory.entries}
    undeclared = sorted(set(index) - declared - control_paths)
    if undeclared:
        raise ReleaseVerificationError(f"undeclared release objects: {', '.join(undeclared)}")

    for entry in inventory.entries:
        if entry.path not in index:
            raise ReleaseVerificationError(f"missing inventory object: {entry.path}")
        if index[entry.path].length != entry.length:
            raise ReleaseVerificationError(f"length mismatch: {entry.path}")

    entries = {entry.path: entry for entry in inventory.entries}
    contents: dict[str, bytes] = {}
    for path in (manifest.frozen_input_path, manifest.preview_path):
        if path not in entries:
            raise ReleaseVerificationError("manifest references an object outside the inventory")
        entry = _require_content(entries[path])
        if entry.length > MAX_CONTROL_BYTES:
            raise ReleaseVerificationError("frozen input or preview exceeds control byte limit")
        _checkpoint(checkpoint)
        contents[path] = b"".join(_verified_chunks(store, entry, checkpoint))

    frozen = _check_bindings(manifest, contents, declared)
    episode_count = len(manifest.episode_mappings)
    frame_count = sum(len(episode.frames) for episode in manifest.episode_mappings)
    for entry in inventory.entries:
        if entry.path in contents:
            continue
        is_lineage = manifest.lineage_stream is not None and entry.path == manifest.lineage_stream.path
        if isinstance(entry, StorageRevisionEntry) and not is_lineage:
            _require_storage_revision(index[entry.path], entry)
            _require_revision_provenance(index[entry.path], entry, manifest, frozen)
            continue
        if isinstance(entry, UploadedContentEntry) and not is_lineage:
            _require_uploaded_content(index[entry.path], entry)
            continue
        content = _require_content(entry)
        _checkpoint(checkpoint)
        chunks = _verified_chunks(store, content, checkpoint)
        if manifest.lineage_stream is not None and is_lineage:
            if content.sha256 != manifest.lineage_stream.sha256:
                raise ReleaseVerificationError("lineage digest does not match inventory binding")
            episode_count, frame_count = _check_lineage(chunks, frozen, manifest.lineage_stream)
        else:
            for _chunk in chunks:
                pass

    return _Package(
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        inventory=inventory,
        inventory_bytes=inventory_bytes,
        contents=contents,
        present=frozenset(index),
        objects=tuple(listed),
        episode_mapping_count=episode_count,
        frame_mapping_count=frame_count,
    )


def _checkpoint(checkpoint: Callable[[], None] | None) -> None:
    if checkpoint is not None:
        checkpoint()


def _require_content(entry: InventoryObject) -> ContentEntry:
    if not isinstance(entry, InventoryEntry | UploadedContentEntry):
        raise ReleaseVerificationError(f"control-bound object requires a content digest: {entry.path}")
    return entry


def _require_uploaded_content(stat: ObjectStat, entry: UploadedContentEntry) -> None:
    if stat.revision is None or stat.copy_operation_sha256 is None or stat.uploaded_sha256 is None:
        raise ReleaseVerificationError(f"uploaded content evidence unavailable: {entry.path}")
    if stat.revision != entry.revision or stat.length != entry.length:
        raise ReleaseVerificationError(f"uploaded content revision changed: {entry.path}")
    if stat.copy_operation_sha256 != entry.operation_sha256 or stat.uploaded_sha256 != entry.sha256:
        raise ReleaseVerificationError(f"uploaded content evidence changed: {entry.path}")


def _require_storage_revision(stat: ObjectStat, entry: StorageRevisionEntry) -> None:
    if stat.revision is None or stat.copy_operation_sha256 is None:
        raise ReleaseVerificationError(f"storage revision evidence unavailable: {entry.path}")
    if stat.revision != entry.revision:
        raise ReleaseVerificationError(f"storage revision changed: {entry.path}")
    if stat.length != entry.length or stat.copy_operation_sha256 != entry.copy_operation_sha256:
        raise ReleaseVerificationError(f"storage copy evidence changed: {entry.path}")


def _require_revision_provenance(
    stat: ObjectStat, entry: StorageRevisionEntry, manifest: ReleaseManifest, frozen: FrozenInput
) -> None:
    mappings = [mapping for mapping in manifest.mappings if mapping.target == entry.path]
    if len(mappings) != 1:
        raise ReleaseVerificationError(f"storage revision object requires one provenance mapping: {entry.path}")
    source = {item.path: item for item in frozen.source_objects}[mappings[0].source]
    if not isinstance(source, RevisionSourceObject):
        raise ReleaseVerificationError(f"storage revision object requires a revision-bound source: {entry.path}")
    if (
        stat.source_revision != source.revision
        or stat.source_identity != source.storage_identity
        or entry.length != source.length
    ):
        raise ReleaseVerificationError(f"storage revision provenance changed: {entry.path}")


def _verified_chunks(
    store: ReleaseStore, entry: ContentEntry, checkpoint: Callable[[], None] | None = None
) -> Iterator[bytes]:
    digest = hashlib.sha256()
    length = 0
    for chunk in store.iter_bytes(entry.path, chunk_size=CHUNK_BYTES):
        _checkpoint(checkpoint)
        length += len(chunk)
        if length > entry.length:
            raise ReleaseVerificationError(f"length mismatch: {entry.path}")
        digest.update(chunk)
        yield chunk
    if length != entry.length or digest.hexdigest() != entry.sha256:
        raise ReleaseVerificationError(f"digest mismatch: {entry.path}")


def _lineage_records(chunks: Iterator[bytes]) -> Iterator[FrameLineageRecord]:
    pending = bytearray()
    for chunk in chunks:
        pending.extend(chunk)
        while (newline := pending.find(b"\n")) >= 0:
            if newline > MAX_LINEAGE_RECORD_BYTES:
                raise ReleaseVerificationError("lineage record exceeds protocol byte limit")
            content = bytes(pending[:newline])
            del pending[: newline + 1]
            record = _parse_model(FrameLineageRecord, content, "frame lineage")
            _require_canonical(content, record, "frame lineage")
            yield record
        if len(pending) > MAX_LINEAGE_RECORD_BYTES:
            raise ReleaseVerificationError("lineage record exceeds protocol byte limit")
    if pending:
        raise ReleaseVerificationError("lineage stream requires a final newline")


def _check_lineage(chunks: Iterator[bytes], frozen: FrozenInput, stream: LineageStream) -> tuple[int, int]:
    episode_index = 0
    next_frame = 0
    previous_source_frame = -1
    count = 0
    for record in _lineage_records(chunks):
        if record.schema_version != stream.record_schema_version:
            raise ReleaseVerificationError("lineage record schema does not match stream binding")
        if record.output_episode_index == episode_index + 1 and next_frame:
            episode_index += 1
            next_frame = 0
            previous_source_frame = -1
        if record.output_episode_index != episode_index or episode_index >= len(frozen.selected_episode_ids):
            raise ReleaseVerificationError("lineage episode order does not match frozen selection")
        if record.source_episode_id != frozen.selected_episode_ids[episode_index]:
            raise ReleaseVerificationError("lineage source episode does not match frozen selection")
        if record.output_frame_index != next_frame or record.source_frame_index <= previous_source_frame:
            raise ReleaseVerificationError("lineage frame indices are not contiguous or increasing")
        next_frame += 1
        previous_source_frame = record.source_frame_index
        count += 1
    episodes = episode_index + 1 if count else 0
    if count != stream.record_count or episodes != stream.episode_count:
        raise ReleaseVerificationError("lineage counts do not match stream binding")
    if episodes != len(frozen.selected_episode_ids):
        raise ReleaseVerificationError("lineage does not cover the frozen episode selection")
    return episodes, count


def _require_unchanged_listing(store: ReleaseStore, package: _Package) -> None:
    if tuple(_list_stable(store)) != package.objects:
        raise ReleaseVerificationError("release object set changed during verification")


def _check_bindings(manifest: ReleaseManifest, contents: dict[str, bytes], declared: set[str]) -> FrozenInput:
    referenced = {
        manifest.frozen_input_path,
        manifest.preview_path,
        *(mapping.target for mapping in manifest.mappings),
    }
    if not referenced <= declared:
        raise ReleaseVerificationError("manifest references an object outside the inventory")
    if manifest.lineage_stream is not None and (
        manifest.lineage_stream.path not in declared or manifest.lineage_stream.path in contents
    ):
        raise ReleaseVerificationError("lineage must reference a distinct inventoried evidence object")

    frozen_bytes = contents[manifest.frozen_input_path]
    if sha256_hex(frozen_bytes) != manifest.frozen_input_sha256:
        raise ReleaseVerificationError("frozen input digest does not match manifest binding")
    frozen = _parse_model(FrozenInput, frozen_bytes, "frozen input")
    _require_canonical(frozen_bytes, frozen, "frozen input")
    source_paths = {source.path for source in frozen.source_objects}
    if any(mapping.source not in source_paths for mapping in manifest.mappings):
        raise ReleaseVerificationError("provenance source is outside the frozen source inventory")
    sources = tuple(episode.source_episode_id for episode in manifest.episode_mappings)
    if manifest.lineage_stream is None and sources != frozen.selected_episode_ids:
        raise ReleaseVerificationError("episode mappings do not match the frozen episode selection")

    preview_bytes = contents[manifest.preview_path]
    if sha256_hex(preview_bytes) != manifest.preview_sha256:
        raise ReleaseVerificationError("preview digest does not match manifest binding")
    preview = _parse_model(ReleasePreview, preview_bytes, "preview")
    _require_canonical(preview_bytes, preview, "preview")
    if preview.frozen_input_sha256 != manifest.frozen_input_sha256:
        raise ReleaseVerificationError("preview is not bound to the frozen input")
    if preview.episode_count != len(frozen.selected_episode_ids):
        raise ReleaseVerificationError("preview episode count does not match frozen selection")
    if any(action.object_path not in declared for action in preview.actions):
        raise ReleaseVerificationError("preview output is outside the inventory")
    return frozen


def _build_checksums(package: _Package) -> tuple[bytes, int, int]:
    """Return sha256sum-format bytes, covered count, and omitted count."""
    covered: dict[str, str] = {MANIFEST_PATH: sha256_hex(package.manifest_bytes)}
    omitted = 0
    for entry in package.inventory.entries:
        if isinstance(entry, StorageRevisionEntry):
            omitted += 1
            continue
        covered[entry.path] = entry.sha256
    lines = "".join(f"{covered[path]}  {path}\n" for path in sorted(covered))
    return lines.encode("utf-8"), len(covered), omitted


def _build_candidate(package: _Package) -> CandidateVerification:
    manifest_sha = sha256_hex(package.manifest_bytes)
    inventory_sha = sha256_hex(package.inventory_bytes)
    package_id = package.manifest.package_id
    checksums_bytes, covered_count, omitted_count = _build_checksums(package)
    receipt = VerificationReceipt(
        package_id=package_id,
        verifier_version=VERIFIER_VERSION,
        assurance=CANDIDATE_ASSURANCE,
        manifest_sha256=manifest_sha,
        inventory_sha256=inventory_sha,
        object_count=len(package.inventory.entries),
        content_digest_count=sum(isinstance(entry, InventoryEntry) for entry in package.inventory.entries),
        uploaded_content_count=sum(isinstance(entry, UploadedContentEntry) for entry in package.inventory.entries),
        storage_revision_count=sum(isinstance(entry, StorageRevisionEntry) for entry in package.inventory.entries),
        checksum_covered_count=covered_count,
        checksum_omitted_count=omitted_count,
        payload_byte_count=sum(entry.length for entry in package.inventory.entries),
        episode_mapping_count=package.episode_mapping_count,
        frame_mapping_count=package.frame_mapping_count,
        checks=RECEIPT_CHECKS,
    )
    result = ReleaseResult(
        identity=PackageIdentity(package_id=package_id, manifest_sha256=manifest_sha),
        publication_state="candidate",
        checksum_covered_count=covered_count,
        checksum_omitted_count=omitted_count,
    )
    return CandidateVerification(
        result=result,
        manifest_sha256=manifest_sha,
        inventory_sha256=inventory_sha,
        receipt_bytes=serialize_model(receipt),
        checksums_bytes=checksums_bytes,
    )


def _require_matching_control(store: ReleaseStore, package: _Package, path: str, expected: bytes) -> None:
    if path in package.present and store.read_control_bytes(path, max_bytes=MAX_CONTROL_BYTES) != expected:
        raise ReleaseVerificationError(f"control artifact does not match regenerated bytes: {path}")


def _require_canonical(content: bytes, model: BaseModel, label: str) -> None:
    if serialize_model(model) != content:
        raise ReleaseVerificationError(f"{label} bytes are not canonical")


def _parse_model[ModelT: BaseModel](model_type: type[ModelT], content: bytes, label: str) -> ModelT:
    try:
        return model_type.model_validate(parse_strict_json(content))
    except ValidationError as exc:
        raise ReleaseVerificationError(f"invalid {label}: {exc.error_count()} validation error(s)") from exc
