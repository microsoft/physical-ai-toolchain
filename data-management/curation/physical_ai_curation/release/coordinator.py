from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import TypeAdapter

from physical_ai_curation.release.blob_worker import SupervisedBlobPublisher
from physical_ai_curation.release.canonical import (
    canonical_json_bytes,
    parse_strict_json,
    serialize_model,
    sha256_hex,
)
from physical_ai_curation.release.composition import (
    NativeGenerationPlan,
    prepare_saved_generation,
)
from physical_ai_curation.release.encoding import EncodeOptions, VideoOutput
from physical_ai_curation.release.execution import encode_planned_object
from physical_ai_curation.release.inputs import (
    BlobReleaseSource,
    FreezeContext,
    FrozenReleaseInputs,
    LocalReleaseSource,
    ReleaseInputError,
    ReleaseSelection,
    revalidate_release_inputs,
)
from physical_ai_curation.release.jobs import ReleaseJobStore
from physical_ai_curation.release.local_worker import SupervisedLocalPublisher
from physical_ai_curation.release.media import (
    MediaWindow,
    ObjectPlanRequest,
    ReleaseMediaLimits,
    StorageEndpoint,
    SubsetEpisodeInput,
    validate_processing_confirmation,
)
from physical_ai_curation.release.models import (
    CONTROL_PATHS,
    INVENTORY_PATH,
    MANIFEST_PATH,
    RECEIPT_PATH,
    REPORT_PATH,
    CapacityEstimate,
    FrameLineageRecord,
    Inventory,
    InventoryEntry,
    LineageStream,
    ProducerIdentity,
    ProvenanceMapping,
    ReleaseManifest,
    ReleasePreview,
    ReleaseResult,
    SourceObject,
    SourceObjectRecord,
    StorageRevisionEntry,
    UploadedContentEntry,
    VersionIdentity,
)
from physical_ai_curation.release.native_inputs import GenerationInputs
from physical_ai_curation.release.projections import materialize_saved_projections
from physical_ai_curation.release.publication import PublicationAttempt
from physical_ai_curation.release.record_storage import BlobLocation
from physical_ai_curation.release.source_format import (
    V21_EPISODE_STATS_PATH,
    control_paths,
    object_path,
    source_version,
    v21_episode_rows,
)
from physical_ai_curation.release.store import LocalReleaseStore, ReleaseStore
from physical_ai_curation.release.verification import verify_candidate, verify_published
from physical_ai_curation.saved_input import SavedInputReader

CHUNK_BYTES = 1024 * 1024
FROZEN_INPUT_PATH = "evidence/frozen-input.json"
PREVIEW_PATH = "evidence/preview.json"
LINEAGE_PATH = "evidence/lineage.jsonl"
INFO_PATH = "meta/info.json"
STATS_PATH = "meta/stats.json"
EPISODES_PATH = "meta/episodes/chunk-000/file-000.parquet"
TASKS_PATH = "meta/tasks.parquet"


def _planning_metadata(
    source: LocalReleaseSource | BlobReleaseSource,
    objects: tuple[SourceObjectRecord, ...],
    metadata_budget: int,
    checkpoint: Callable[[], None] | None,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    if type(metadata_budget) is not int or metadata_budget <= 0:
        raise ReleaseInputError("native metadata budget must be positive")
    identities = {item.path: item for item in objects}
    remaining = (
        min(metadata_budget, source.metadata_max_bytes) if isinstance(source, BlobReleaseSource) else metadata_budget
    )
    row_limit = source.metadata_max_rows if isinstance(source, BlobReleaseSource) else 10000

    def read(path: str) -> bytes:
        nonlocal remaining
        _checkpoint(checkpoint)
        identity = identities.get(path)
        if identity is None or identity.length > remaining:
            raise ReleaseInputError("native planning metadata exceeds its budget or is missing")
        chunks = (
            source.iter_bytes(identity, chunk_size=CHUNK_BYTES, checkpoint=checkpoint)
            if isinstance(source, BlobReleaseSource)
            else LocalReleaseStore(source.root).iter_bytes(path, chunk_size=CHUNK_BYTES)
        )
        content = bytearray()
        for chunk in chunks:
            _checkpoint(checkpoint)
            if len(content) + len(chunk) > identity.length:
                raise ReleaseInputError("native planning metadata changed")
            content.extend(chunk)
        remaining -= len(content)
        if len(content) != identity.length or (
            isinstance(identity, SourceObject) and sha256_hex(bytes(content)) != identity.sha256
        ):
            raise ReleaseInputError("native planning metadata changed")
        return bytes(content)

    info = parse_strict_json(read(INFO_PATH))
    if not isinstance(info, dict):
        raise ReleaseInputError("native dataset info must be an object")
    if source_version(info) == "v2.1":
        rows = v21_episode_rows(info, read, max_rows=row_limit, has_statistics=V21_EPISODE_STATS_PATH in identities)
        if len(rows) > row_limit:
            raise ReleaseInputError("native planning metadata exceeds its row budget")
        return info, rows
    rows = []
    for path in sorted(identities):
        _checkpoint(checkpoint)
        if not path.startswith("meta/episodes/") or not path.endswith(".parquet"):
            continue
        parquet = pq.ParquetFile(pa.BufferReader(read(path)))
        if parquet.metadata.num_rows > row_limit - len(rows):
            raise ReleaseInputError("native planning metadata exceeds its row budget")
        decoded_bytes = sum(
            parquet.metadata.row_group(index).total_byte_size for index in range(parquet.metadata.num_row_groups)
        )
        if decoded_bytes * 3 > remaining:
            raise ReleaseInputError("native planning metadata exceeds its decoded byte budget")
        table = parquet.read()
        if table.nbytes * 3 > remaining:
            raise ReleaseInputError("native planning tables exceed their budget")
        remaining -= table.nbytes * 3
        rows.extend(table.to_pylist())
    return info, rows


def select_blob_dependencies(
    source: BlobReleaseSource,
    episode_indices: tuple[int, ...],
    *,
    metadata_budget: int = 16 * 1024**2,
    checkpoint: Callable[[], None] | None = None,
) -> BlobReleaseSource:
    """Discover selected shared objects from bounded metadata, without payload listing or reads."""
    metadata = source.metadata_inventory(checkpoint)
    info, rows = _planning_metadata(source, metadata, metadata_budget, checkpoint)
    by_episode = {row["episode_index"]: row for row in rows}
    if len(by_episode) != len(rows) or not episode_indices or any(index not in by_episode for index in episode_indices):
        raise ReleaseInputError("native selected episode metadata is missing or ambiguous")
    paths = {INFO_PATH, *control_paths(info, tuple(item.path for item in metadata))}
    prefixes = [
        "data",
        *(f"videos/{name}" for name, feature in info["features"].items() if feature.get("dtype") == "video"),
    ]
    for index in episode_indices:
        row = by_episode[index]
        for prefix in prefixes:
            paths.add(object_path(info, row, prefix))
    selected = source.scoped(sorted(paths))
    observed = {item.path: item for item in selected.inventory(checkpoint)}
    if any(observed[item.path] != item for item in metadata if item.path in paths):
        raise ReleaseInputError("native planning metadata changed during selection")
    return selected


def plan_generation_inputs(
    source: LocalReleaseSource | BlobReleaseSource,
    frozen: FrozenReleaseInputs,
    *,
    source_endpoint: StorageEndpoint,
    destination_endpoint: StorageEndpoint,
    scratch_endpoint: StorageEndpoint | None,
    producer: ProducerIdentity,
    tool: VersionIdentity,
    scratch_bytes: int,
    output_bytes: int,
    limits: ReleaseMediaLimits | None = None,
    encode_options: EncodeOptions | None = None,
    metadata_budget: int = 16 * 1024**2,
    checkpoint: Callable[[], None] | None = None,
) -> GenerationInputs:
    """Plan from bounded frozen metadata; only a claimed worker restores native frame buffers."""
    info, rows = _planning_metadata(source, frozen.content.source_objects, metadata_budget, checkpoint)
    identities = {item.path: item for item in frozen.content.source_objects}
    cameras = sorted(name for name, feature in info["features"].items() if feature.get("dtype") == "video")
    by_episode = {row["episode_index"]: row for row in rows}
    if len(by_episode) != len(rows):
        raise ReleaseInputError("native episode metadata is ambiguous")
    durations = {}

    def video_path(camera: str, row: Mapping[str, object]) -> str:
        return object_path(info, row, f"videos/{camera}")

    for row in rows:
        for camera in cameras:
            path = video_path(camera, row)
            durations[path] = max(durations.get(path, 0), row[f"videos/{camera}/to_timestamp"])
    sources, summaries, windows = {}, {}, []
    output_paths: dict[str, str] = {}
    for output_index, episode in enumerate(frozen.content.selected_episode_ids):
        suffix = episode.rsplit("/episode_", 1)
        if len(suffix) != 2 or not suffix[1].isdigit():
            raise ReleaseInputError("native selected episode identity is invalid")
        index = int(suffix[1])
        row = by_episode.get(index)
        if row is None:
            raise ReleaseInputError("native selected episode is missing")
        sources[episode] = SubsetEpisodeInput(episode, index, row["length"], float(info["fps"]))
        summaries[episode] = {}
        for camera in cameras:
            path = video_path(camera, row)
            identity = identities.get(path)
            if identity is None:
                raise ReleaseInputError("native camera object is missing")
            if info["codebase_version"] == "v2.1":
                output_paths[path] = (
                    f"videos/{camera}/chunk-{output_index // 1000:03d}/file-{output_index % 1000:03d}.mp4"
                )
            windows.append(
                MediaWindow(
                    episode,
                    camera,
                    identity,
                    row[f"videos/{camera}/from_timestamp"],
                    row[f"videos/{camera}/to_timestamp"],
                    durations[path],
                )
            )
            summary = {
                name: row[f"stats/{camera}/{name}"]
                for name in ("min", "max", "mean", "std", "count")
                if row.get(f"stats/{camera}/{name}") is not None
            }
            if summary:
                summaries[episode][camera] = summary
    request = ObjectPlanRequest(
        source=source_endpoint,
        destination=destination_endpoint,
        scratch=scratch_endpoint,
        target_format=VersionIdentity(name="lerobot", version="v3.0"),
        frozen_input_sha256=frozen.sha256,
        selected_episode_ids=frozen.content.selected_episode_ids,
        windows=tuple(windows),
        tool=tool,
        source_objects=frozen.content.source_objects,
        limits=limits if limits is not None else ReleaseMediaLimits(),
        output_paths=tuple(sorted(output_paths.items())),
    )
    prepared = prepare_saved_generation(
        frozen.content,
        request,
        sources,
        encode_options=encode_options,
        camera_statistics=summaries,
    )
    return GenerationInputs(
        request=request,
        sources=sources,
        source_root=source.location if isinstance(source, BlobReleaseSource) else source.root,
        source_info=info,
        source_tables={},
        source_tasks=pa.table({}),
        camera_statistics=summaries,
        preserved_dataset_statistics=None,
        encode_options=encode_options,
        producer=producer.model_copy(
            update={"source_format": VersionIdentity(name="lerobot", version=source_version(info))}
        ),
        confirmed_digest=prepared.plan.digest,
        scratch_bytes=scratch_bytes,
        output_bytes=output_bytes,
    )


def generation_request_bytes(frozen: FrozenReleaseInputs, inputs: GenerationInputs) -> bytes:
    """Canonical durable acceptance binding, excluding regenerated native table buffers."""
    return canonical_json_bytes(
        {
            "schema_version": "curated-release-generation/v1",
            "frozen": TypeAdapter(FrozenReleaseInputs).dump_python(frozen, mode="json"),
            "request": TypeAdapter(ObjectPlanRequest).dump_python(inputs.request, mode="json"),
            "sources": TypeAdapter(dict[str, SubsetEpisodeInput]).dump_python(dict(inputs.sources), mode="json"),
            "camera_statistics": inputs.camera_statistics,
            "preserved_dataset_statistics": inputs.preserved_dataset_statistics,
            "encode_options": TypeAdapter(EncodeOptions | None).dump_python(inputs.encode_options, mode="json"),
            "producer": inputs.producer.model_dump(mode="json"),
            "confirmed_digest": inputs.confirmed_digest,
            "scratch_bytes": inputs.scratch_bytes,
            "output_bytes": inputs.output_bytes,
        }
    )


@dataclass(frozen=True)
class _Effects:
    publisher: SupervisedLocalPublisher | SupervisedBlobPublisher
    store: ReleaseJobStore
    job_id: str
    fence: int


def _parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink)
    return sink.getvalue().to_pybytes()


def _encoded_files(
    effects: _Effects,
    prepared: NativeGenerationPlan,
    inputs: GenerationInputs,
    source: LocalReleaseSource | BlobReleaseSource,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, VideoOutput]:
    """Encode each planned transcode, write its bytes privately, and return the media results."""
    media: dict[str, VideoOutput] = {}
    identities = {item.path: item for item in inputs.request.source_objects}
    for obj in prepared.plan.objects:
        if obj.action != "transcode":
            continue
        assert obj.source_path is not None
        chunks = (
            source.iter_bytes(identities[obj.source_path], chunk_size=CHUNK_BYTES)
            if isinstance(source, BlobReleaseSource)
            else LocalReleaseStore(source.root).iter_bytes(obj.source_path, chunk_size=CHUNK_BYTES)
        )
        with encode_planned_object(
            effects.store,
            effects.job_id,
            effects.fence,
            prepared,
            obj,
            chunks,
            confirmed_digest=inputs.confirmed_digest,
            scratch_bytes=inputs.scratch_bytes,
            output_bytes=inputs.output_bytes,
            limits=inputs.request.limits,
            checkpoint=checkpoint,
        ) as encoded:
            if isinstance(source, BlobReleaseSource):
                effects.store.record_io(
                    effects.job_id,
                    effects.fence,
                    uuid4().hex,
                    {"affected_download_bytes": encoded.source_verification_bytes},
                )
            effects.publisher.write_from_path(
                obj.output_path,
                encoded.path,
                length=encoded.length,
                sha256=encoded.sha256,
            )
            media[obj.output_path] = encoded.media
    return media


def _lineage(prepared: NativeGenerationPlan) -> Iterator[bytes]:
    for output_episode_index, episode in enumerate(prepared.episodes):
        output_frame = 0
        for source_frame in range(episode.length):
            if source_frame in episode.removed_frames:
                continue
            record = FrameLineageRecord(
                output_episode_index=output_episode_index,
                source_episode_id=episode.episode_id,
                output_frame_index=output_frame,
                source_frame_index=source_frame,
            )
            yield serialize_model(record) + b"\n"
            output_frame += 1


def _lineage_identity(prepared: NativeGenerationPlan) -> tuple[int, str, int]:
    digest = hashlib.sha256()
    length = count = 0
    for line in _lineage(prepared):
        digest.update(line)
        length += len(line)
        count += 1
    return length, digest.hexdigest(), count


def _checkpoint(checkpoint: Callable[[], None] | None) -> None:
    if checkpoint is not None:
        checkpoint()


def _inventory(store: ReleaseStore, *, checkpoint: Callable[[], None] | None = None) -> Inventory:
    entries = []
    for stat in sorted(store.list_objects(), key=lambda item: item.path):
        if stat.path in CONTROL_PATHS:
            continue
        _checkpoint(checkpoint)
        if stat.source_revision is not None:
            if stat.revision is None or stat.copy_operation_sha256 is None:
                raise ReleaseInputError("storage revision evidence unavailable")
            entries.append(
                StorageRevisionEntry(
                    path=stat.path,
                    length=stat.length,
                    revision=stat.revision,
                    copy_operation_sha256=stat.copy_operation_sha256,
                )
            )
            continue
        if stat.uploaded_sha256 is not None:
            if stat.revision is None or stat.copy_operation_sha256 is None:
                raise ReleaseInputError("upload revision evidence unavailable")
            entries.append(
                UploadedContentEntry(
                    path=stat.path,
                    length=stat.length,
                    revision=stat.revision,
                    sha256=stat.uploaded_sha256,
                    operation_sha256=stat.copy_operation_sha256,
                )
            )
            continue
        digest = hashlib.sha256()
        for chunk in store.iter_bytes(stat.path, chunk_size=CHUNK_BYTES):
            _checkpoint(checkpoint)
            digest.update(chunk)
        entries.append(InventoryEntry(path=stat.path, length=stat.length, sha256=digest.hexdigest()))
    return Inventory(entries=tuple(entries))


@contextmanager
def _publication_runtime(
    destination: Path | BlobLocation,
    attempt: PublicationAttempt,
    store: ReleaseJobStore,
    job_id: str,
    fence: int,
    supplied: SupervisedLocalPublisher | SupervisedBlobPublisher | None,
) -> Iterator[SupervisedLocalPublisher | SupervisedBlobPublisher]:
    association = store.source_association(job_id)
    from physical_ai_curation.release.blob_worker import SourceAssociatedBlobPublisher
    from physical_ai_curation.release.local_worker import SourceAssociatedLocalPublisher

    associated = (SourceAssociatedLocalPublisher, SourceAssociatedBlobPublisher)
    if supplied is not None:
        actual = supplied.location if isinstance(supplied, SupervisedBlobPublisher) else supplied.destination
        if (
            actual != destination
            or supplied.attempt != attempt
            or (supplied.jobs, supplied.job_id, supplied.fence) != (store, job_id, fence)
        ):
            raise ReleaseInputError("publisher does not match the accepted execution binding")
        if association is not None and not isinstance(supplied, associated):
            raise ReleaseInputError("source-associated job requires its accepted publisher")
        yield supplied
    elif isinstance(destination, Path):
        if association is None:
            yield SupervisedLocalPublisher(destination, attempt, store, job_id, fence)
        else:
            yield SourceAssociatedLocalPublisher(destination, attempt, store, job_id, fence, association)
    else:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import ContainerClient

        with (
            DefaultAzureCredential() as credential,
            ContainerClient(
                destination.account_url,
                destination.container,
                credential=credential,
                connection_timeout=store.settings.recovery_seconds,
                read_timeout=store.settings.recovery_seconds,
                retry_total=0,
            ) as container,
        ):
            if association is None:
                yield SupervisedBlobPublisher(container, destination, attempt, store, job_id, fence)
            else:
                yield SourceAssociatedBlobPublisher(container, destination, attempt, store, job_id, fence, association)


async def run_release_generation(
    *,
    store: ReleaseJobStore,
    job_id: str,
    fence: int,
    destination: Path | BlobLocation,
    attempt: PublicationAttempt,
    expected: FrozenReleaseInputs,
    source: LocalReleaseSource | BlobReleaseSource,
    reader: SavedInputReader,
    selection: ReleaseSelection,
    context: FreezeContext,
    inputs: GenerationInputs,
    publisher: SupervisedLocalPublisher | SupervisedBlobPublisher | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> ReleaseResult:
    """Publish one confirmed saved selection; every effect follows revalidation and confirmation."""
    route = "blob" if isinstance(destination, BlobLocation) else "local"
    store.validate_execution_binding(job_id, fence, destination, attempt, route=route)
    if isinstance(source, LocalReleaseSource) and isinstance(destination, Path):
        source_root, release_root = (
            source.root.resolve(),
            (destination / attempt.prefix).resolve(),
        )
        if source_root == release_root or source_root in release_root.parents or release_root in source_root.parents:
            raise ReleaseInputError("source and release destination overlap")
    elif isinstance(source, BlobReleaseSource) and isinstance(destination, BlobLocation):
        location = source.location
        if location.account_url != destination.account_url:
            raise ReleaseInputError("cross-account generation is unsupported")
        original = location.prefix.rstrip("/") + "/"
        output = (destination.prefix + "/" if destination.prefix else "") + attempt.prefix + "/"
        if location.container == destination.container and (
            not location.prefix or original.startswith(output) or output.startswith(original)
        ):
            raise ReleaseInputError("source and release destination overlap")
    else:
        raise ReleaseInputError("source and destination route is unsupported")
    source_read_bytes = 0

    def verified(length: int) -> None:
        nonlocal source_read_bytes
        source_read_bytes += length

    observed_source = (
        LocalReleaseSource(source.root, finalized=source.finalized, on_verified=verified)
        if isinstance(source, LocalReleaseSource)
        else source.scoped(item.path for item in expected.content.source_objects)
    )
    frozen = await revalidate_release_inputs(
        expected, observed_source, reader, selection, context, checkpoint=checkpoint
    )
    store.record_io(job_id, fence, uuid4().hex, {"source_verification_bytes": source_read_bytes})
    if store.frozen_request(job_id) != generation_request_bytes(frozen, inputs):
        raise ReleaseInputError("generation does not match the durably accepted frozen request")
    prepared = prepare_saved_generation(
        frozen.content,
        inputs.request,
        inputs.sources,
        encode_options=inputs.encode_options,
        camera_statistics=inputs.camera_statistics,
    )
    validate_processing_confirmation(prepared.plan, confirmed_digest=inputs.confirmed_digest)
    projections = materialize_saved_projections(frozen.content, prepared.episodes, release_dataset_id=attempt.prefix)
    projection_bytes = sum(len(payload) for payload in projections.values())
    if prepared.plan.capacity.payload_bytes + projection_bytes > inputs.output_bytes:
        raise ReleaseInputError("projection output exceeds the accepted destination budget")

    with _publication_runtime(destination, attempt, store, job_id, fence, publisher) as selected:
        return _publish_generation(
            store,
            job_id,
            fence,
            attempt,
            frozen,
            prepared,
            inputs,
            source,
            selected,
            projections,
            checkpoint,
        )


def _generated_batch(
    publisher: SupervisedLocalPublisher | SupervisedBlobPublisher,
) -> AbstractContextManager[object]:
    """Group generated blob writes for one bounded batch dispatch; local publishers write directly."""
    if isinstance(publisher, SupervisedBlobPublisher):
        return publisher.write_batch()
    return nullcontext()


def _publish_generation(
    store: ReleaseJobStore,
    job_id: str,
    fence: int,
    attempt: PublicationAttempt,
    frozen: FrozenReleaseInputs,
    prepared: NativeGenerationPlan,
    inputs: GenerationInputs,
    source: LocalReleaseSource | BlobReleaseSource,
    publisher: SupervisedLocalPublisher | SupervisedBlobPublisher,
    projections: Mapping[str, bytes],
    checkpoint: Callable[[], None] | None = None,
) -> ReleaseResult:
    from physical_ai_curation.release.native_worker import native_operation

    effects = _Effects(publisher=publisher, store=store, job_id=job_id, fence=fence)
    source_objects = {obj.path: obj for obj in frozen.content.source_objects}

    copies = [obj for obj in prepared.plan.objects if obj.action == "copy"]
    if isinstance(source, BlobReleaseSource) and isinstance(publisher, SupervisedBlobPublisher):
        blob_items = []
        for obj in copies:
            assert obj.source_path is not None
            identity = source_objects[obj.source_path]
            bound = source.binding(identity)
            blob_items.append((identity.model_copy(update={"path": obj.output_path}), bound.etag, bound.name))
        publisher.copy_batch(source.container_client, blob_items)
    else:
        for obj in copies:
            assert obj.source_path is not None
            identity = source_objects[obj.source_path]
            if (
                isinstance(source, LocalReleaseSource)
                and isinstance(publisher, SupervisedLocalPublisher)
                and isinstance(identity, SourceObject)
            ):
                publisher.copy(source.root, identity, output_path=obj.output_path)
            else:
                raise ReleaseInputError("copy route does not match the accepted execution")
    media = _encoded_files(effects, prepared, inputs, source, checkpoint)
    with (
        native_operation(store, job_id, fence, frozen, prepared, inputs, mode="assemble", media=media) as objects,
        _generated_batch(publisher),
    ):
        for path, output, length, digest in objects:
            publisher.write_from_path(path, output, length=length, sha256=digest)

    with _generated_batch(publisher):
        frozen_payload = serialize_model(frozen.content)
        publisher.write(
            FROZEN_INPUT_PATH,
            [frozen_payload],
            length=len(frozen_payload),
            sha256=sha256_hex(frozen_payload),
        )
        preview = ReleasePreview(
            frozen_input_sha256=frozen.sha256,
            episode_count=len(prepared.episodes),
            warnings=prepared.plan.warnings,
            actions=prepared.plan.to_preview_actions(),
            capacity=CapacityEstimate(
                estimated_object_count=len(prepared.plan.objects) + len(projections),
                estimated_output_bytes=sum(obj.estimated_output_bytes for obj in prepared.plan.objects)
                + sum(len(payload) for payload in projections.values()),
            ),
        )
        preview_payload = serialize_model(preview)
        publisher.write(
            PREVIEW_PATH,
            [preview_payload],
            length=len(preview_payload),
            sha256=sha256_hex(preview_payload),
        )
        lineage_length, lineage_digest, record_count = _lineage_identity(prepared)
        publisher.write_lineage(LINEAGE_PATH, prepared, length=lineage_length, sha256=lineage_digest)
        for projection_path, projection in projections.items():
            publisher.write(
                projection_path,
                [projection],
                length=len(projection),
                sha256=sha256_hex(projection),
            )

    local_store = publisher.store
    inventory = _inventory(local_store, checkpoint=checkpoint)
    inventory_payload = serialize_model(inventory)
    publisher.write(
        INVENTORY_PATH,
        [inventory_payload],
        length=len(inventory_payload),
        sha256=sha256_hex(inventory_payload),
    )
    mappings = tuple(
        ProvenanceMapping(source=obj.source_path, target=obj.output_path)
        for obj in prepared.plan.objects
        if obj.action in {"copy", "transcode"} and obj.source_path is not None
    )
    manifest = ReleaseManifest(
        package_id=attempt.package_id,
        producer=inputs.producer,
        frozen_input_path=FROZEN_INPUT_PATH,
        frozen_input_sha256=frozen.sha256,
        preview_path=PREVIEW_PATH,
        preview_sha256=sha256_hex(preview_payload),
        inventory_sha256=sha256_hex(inventory_payload),
        mappings=mappings,
        episode_mappings=(),
        lineage_stream=LineageStream(
            path=LINEAGE_PATH,
            sha256=lineage_digest,
            record_count=record_count,
            episode_count=len(prepared.episodes),
        ),
    )
    manifest_payload = serialize_model(manifest)
    publisher.write(
        MANIFEST_PATH,
        [manifest_payload],
        length=len(manifest_payload),
        sha256=sha256_hex(manifest_payload),
    )

    candidate = verify_candidate(local_store, sha256_hex(manifest_payload), checkpoint=checkpoint)
    store.seal(
        job_id,
        fence,
        {
            RECEIPT_PATH: (
                len(candidate.receipt_bytes),
                sha256_hex(candidate.receipt_bytes),
            ),
            REPORT_PATH: (
                len(candidate.report_bytes),
                sha256_hex(candidate.report_bytes),
            ),
        },
    )
    publisher.publish(candidate.manifest_sha256)
    result = verify_published(publisher.store, candidate.manifest_sha256, checkpoint=checkpoint)
    store.record_io(
        job_id,
        fence,
        uuid4().hex,
        {"destination_verification_bytes": publisher.destination_read_bytes},
    )
    return result
