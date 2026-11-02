from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import TypeAdapter

from physical_ai_curation.release.blob_worker import SupervisedBlobPublisher
from physical_ai_curation.release.canonical import canonical_json_bytes, parse_strict_json, serialize_model, sha256_hex
from physical_ai_curation.release.composition import NativeGenerationPlan, prepare_saved_generation
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
    VersionIdentity,
)
from physical_ai_curation.release.publication import PublicationAttempt
from physical_ai_curation.release.record_storage import BlobLocation
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


@dataclass(frozen=True)
class GenerationInputs:
    """Caller-supplied source identity, tables, and settings for one confirmed release."""

    request: ObjectPlanRequest
    sources: Mapping[str, SubsetEpisodeInput]
    source_root: Path | BlobLocation
    source_info: Mapping[str, object]
    source_tables: Mapping[str, pa.Table]
    source_tasks: pa.Table
    camera_statistics: Mapping[str, Mapping[str, Mapping[str, object]]] | None
    preserved_dataset_statistics: Mapping[str, Mapping[str, object]] | None
    encode_options: EncodeOptions | None
    producer: ProducerIdentity
    confirmed_digest: str
    scratch_bytes: int
    output_bytes: int
    native_verified_bytes: int = 0
    native_table_identities: Mapping[str, str] | None = None
    native_task_identity: str | None = None


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
) -> GenerationInputs:
    """Plan from frozen v3 metadata without decoding media or reserving output.

    Source/destination endpoints and budgets come from trusted configuration.
    Returned native frame buffers are empty: use ``restore_inputs`` in a claimed
    worker before execution. Fresh native validation still checks these derived
    descriptors, windows and statistics against the frozen bytes.
    """
    if type(metadata_budget) is not int or metadata_budget <= 0:
        raise ReleaseInputError("native metadata budget must be positive")
    identities = {item.path: item for item in frozen.content.source_objects}
    remaining = metadata_budget

    def read(path: str) -> bytes:
        nonlocal remaining
        identity = identities.get(path)
        if identity is None or identity.length > remaining:
            raise ReleaseInputError("native planning metadata exceeds its budget or is missing")
        chunks = (
            source.iter_bytes(identity, chunk_size=CHUNK_BYTES)
            if isinstance(source, BlobReleaseSource)
            else LocalReleaseStore(source.root).iter_bytes(path, chunk_size=CHUNK_BYTES)
        )
        content = bytearray()
        for chunk in chunks:
            if len(content) + len(chunk) > identity.length:
                raise ReleaseInputError("native planning metadata changed")
            content.extend(chunk)
        remaining -= len(content)
        if len(content) != identity.length or sha256_hex(bytes(content)) != identity.sha256:
            raise ReleaseInputError("native planning metadata changed")
        return bytes(content)

    info = parse_strict_json(read(INFO_PATH))
    if not isinstance(info, dict) or info.get("codebase_version") != "v3.0":
        raise ReleaseInputError("only native LeRobot v3.0 planning is supported")
    cameras = sorted(name for name, feature in info["features"].items() if feature.get("dtype") == "video")
    rows = []
    for path in sorted(identities):
        if not path.startswith("meta/episodes/") or not path.endswith(".parquet"):
            continue
        parquet = pq.ParquetFile(pa.BufferReader(read(path)))
        if parquet.metadata.num_rows > 10000 - len(rows):
            raise ReleaseInputError("native planning supports at most 10000 source episodes")
        table = parquet.read()
        if table.nbytes * 3 > remaining:
            raise ReleaseInputError("native planning tables exceed their budget")
        remaining -= table.nbytes * 3
        rows.extend(table.to_pylist())
    by_episode = {row["episode_index"]: row for row in rows}
    if len(by_episode) != len(rows):
        raise ReleaseInputError("native episode metadata is ambiguous")
    durations = {}

    def video_path(camera: str, row: Mapping[str, object]) -> str:
        prefix = f"videos/{camera}"
        chunk, file = row.get(f"{prefix}/chunk_index"), row.get(f"{prefix}/file_index")
        if type(chunk) is not int or chunk < 0 or type(file) is not int or file < 0:
            raise ReleaseInputError("native media index is invalid")
        return f"{prefix}/chunk-{chunk:03d}/file-{file:03d}.mp4"

    for row in rows:
        for camera in cameras:
            path = video_path(camera, row)
            durations[path] = max(durations.get(path, 0), row[f"videos/{camera}/to_timestamp"])
    sources, summaries, windows = {}, {}, []
    for episode in frozen.content.selected_episode_ids:
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
                if f"stats/{camera}/{name}" in row
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
    )
    prepared = prepare_saved_generation(
        frozen.content, request, sources, encode_options=encode_options, camera_statistics=summaries
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
        producer=producer,
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


def _validate_native_inputs(
    source: LocalReleaseSource | BlobReleaseSource,
    frozen: FrozenReleaseInputs,
    inputs: GenerationInputs,
    budget: int,
    *,
    table_identities: Mapping[str, str] | None = None,
    task_identity: str | None = None,
    restore: bool = False,
) -> GenerationInputs:
    from physical_ai_curation.release.native_worker import table_identity

    local_store: LocalReleaseStore | None = None
    if isinstance(source, LocalReleaseSource):
        if not isinstance(inputs.source_root, Path) or source.root.resolve() != inputs.source_root.resolve():
            raise ReleaseInputError("native inputs are not condition-bound to the selected source")
        local_store = LocalReleaseStore(source.root)
    elif inputs.source_root != source.location:
        raise ReleaseInputError("native inputs are not condition-bound to the selected source")
    identities = {item.path: item for item in frozen.content.source_objects}
    charged = 0
    verified_bytes = 0

    def fetch(identity: SourceObject) -> bytes:
        if local_store is not None:
            return local_store.read_control_bytes(identity.path, max_bytes=budget - charged)
        assert isinstance(source, BlobReleaseSource)
        return b"".join(source.iter_bytes(identity, chunk_size=CHUNK_BYTES))

    def read(path: str) -> bytes:
        nonlocal charged, verified_bytes
        identity = identities.get(path)
        if identity is None or identity.length > budget - charged:
            raise ReleaseInputError("condition-bound native inputs exceed the memory budget")
        content = fetch(identity)
        charged += len(content)
        if len(content) != identity.length or sha256_hex(content) != identity.sha256:
            raise ReleaseInputError("condition-bound native input changed")
        verified_bytes += len(content)
        return content

    def table(path: str) -> pa.Table:
        nonlocal charged
        result = pq.read_table(pa.BufferReader(read(path)))
        charged += result.nbytes * 3
        if charged > budget:
            raise ReleaseInputError("condition-bound native tables exceed the memory budget")
        return result

    def native_path(prefix: str, row: Mapping[str, object]) -> str:
        chunk, file = row.get(f"{prefix}/chunk_index"), row.get(f"{prefix}/file_index")
        if type(chunk) is not int or chunk < 0 or type(file) is not int or file < 0:
            raise ReleaseInputError("condition-bound native object index is invalid")
        extension = "mp4" if prefix.startswith("videos/") else "parquet"
        return f"{prefix}/chunk-{chunk:03d}/file-{file:03d}.{extension}"

    info = parse_strict_json(read(INFO_PATH))
    tasks = table(TASKS_PATH)
    rows = []
    for path in sorted(identities):
        if path.startswith("meta/episodes/") and path.endswith(".parquet"):
            rows.extend(table(path).to_pylist())
    by_episode = {row["episode_index"]: row for row in rows}
    if len(by_episode) != len(rows):
        raise ReleaseInputError("condition-bound native episode metadata is ambiguous")
    tasks_match = (
        table_identity(tasks) == task_identity if task_identity is not None else tasks.equals(inputs.source_tasks)
    )
    if not restore and (info != inputs.source_info or not tasks_match):
        raise ReleaseInputError("native metadata is not condition-bound to frozen bytes")
    cameras = sorted(name for name, feature in info["features"].items() if feature.get("dtype") == "video")
    durations = {}
    for row in rows:
        for camera in cameras:
            path = native_path(f"videos/{camera}", row)
            durations[path] = max(durations.get(path, 0), row[f"videos/{camera}/to_timestamp"])
    expected_windows = []
    summaries = {}
    loaded = {}
    selected_tables = {}
    for episode in frozen.content.selected_episode_ids:
        descriptor = inputs.sources.get(episode)
        if descriptor is None:
            raise ReleaseInputError("condition-bound source episode is missing")
        row = by_episode.get(descriptor.original_episode_index)
        if (
            row is None
            or descriptor.length != row["length"]
            or descriptor.fps != info["fps"]
            or descriptor.removed_frames
        ):
            raise ReleaseInputError("source descriptor is not condition-bound to native metadata")
        path = native_path("data", row)
        if path not in loaded:
            loaded[path] = table(path)
        native = loaded[path]
        indices = pa.compute.equal(native["episode_index"], descriptor.original_episode_index)
        selected = native.filter(indices)
        supplied = inputs.source_tables.get(episode)
        matches = (
            table_identity(selected) == table_identities.get(episode)
            if table_identities is not None
            else supplied is not None and selected.equals(supplied)
        )
        if not restore and not matches:
            raise ReleaseInputError("native table is not condition-bound to frozen bytes")
        selected_tables[episode] = selected
        summaries[episode] = {}
        for camera in cameras:
            path = native_path(f"videos/{camera}", row)
            identity = identities.get(path)
            if identity is None:
                raise ReleaseInputError("condition-bound camera object is missing")
            expected_windows.append(
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
                if f"stats/{camera}/{name}" in row
            }
            if summary:
                summaries[episode][camera] = summary

    def key(item: MediaWindow) -> tuple[str, str]:
        return item.episode_id, item.camera

    if sorted(expected_windows, key=key) != sorted(inputs.request.windows, key=key):
        raise ReleaseInputError("camera windows are not condition-bound to native metadata")
    if (inputs.camera_statistics or {}) != summaries:
        raise ReleaseInputError("camera statistics are not condition-bound to native metadata")
    if (
        inputs.preserved_dataset_statistics is not None
        and parse_strict_json(read(STATS_PATH)) != inputs.preserved_dataset_statistics
    ):
        raise ReleaseInputError("dataset statistics are not condition-bound to frozen bytes")
    return replace(
        inputs,
        source_info=info,
        source_tables=selected_tables,
        source_tasks=tasks,
        native_verified_bytes=verified_bytes,
    )


def _parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink)
    return sink.getvalue().to_pybytes()


def _data_path(index: int) -> str:
    return f"data/chunk-{index // 1000:03d}/file-{index % 1000:03d}.parquet"


def _encoded_files(
    effects: _Effects,
    prepared: NativeGenerationPlan,
    inputs: GenerationInputs,
    source: LocalReleaseSource | BlobReleaseSource,
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
        ) as encoded:
            if isinstance(source, BlobReleaseSource):
                effects.store.record_io(
                    effects.job_id,
                    effects.fence,
                    uuid4().hex,
                    {"affected_download_bytes": encoded.source_verification_bytes},
                )
            effects.publisher.write_from_path(
                obj.output_path, encoded.path, length=encoded.length, sha256=encoded.sha256
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


def _inventory(store: ReleaseStore) -> Inventory:
    entries = []
    for stat in sorted(store.list_objects(), key=lambda item: item.path):
        if stat.path in CONTROL_PATHS:
            continue
        digest = hashlib.sha256()
        for chunk in store.iter_bytes(stat.path, chunk_size=CHUNK_BYTES):
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
    if supplied is not None:
        actual = supplied.location if isinstance(supplied, SupervisedBlobPublisher) else supplied.destination
        if (
            actual != destination
            or supplied.attempt != attempt
            or (supplied.jobs, supplied.job_id, supplied.fence) != (store, job_id, fence)
        ):
            raise ReleaseInputError("publisher does not match the accepted execution binding")
        yield supplied
    elif isinstance(destination, Path):
        yield SupervisedLocalPublisher(destination, attempt, store, job_id, fence)
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
            yield SupervisedBlobPublisher(container, destination, attempt, store, job_id, fence)


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
) -> ReleaseResult:
    """Publish one confirmed saved selection; every effect follows revalidation and confirmation."""
    route = "blob" if isinstance(destination, BlobLocation) else "local"
    store.validate_execution_binding(job_id, fence, destination, attempt, route=route)
    if isinstance(source, LocalReleaseSource) and isinstance(destination, Path):
        source_root, release_root = source.root.resolve(), (destination / attempt.prefix).resolve()
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
        else source
    )
    frozen = await revalidate_release_inputs(expected, observed_source, reader, selection, context)
    if isinstance(source, BlobReleaseSource):
        source_read_bytes = sum(item.length for item in frozen.content.source_objects)
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

    from physical_ai_curation.release.native_worker import native_operation

    with native_operation(store, job_id, fence, frozen, prepared, inputs, mode="validate"):
        pass

    with _publication_runtime(destination, attempt, store, job_id, fence, publisher) as selected:
        return _publish_generation(store, job_id, fence, attempt, frozen, prepared, inputs, source, selected)


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
) -> ReleaseResult:
    from physical_ai_curation.release.native_worker import native_operation

    effects = _Effects(publisher=publisher, store=store, job_id=job_id, fence=fence)
    source_objects = {obj.path: obj for obj in frozen.content.source_objects}

    media = _encoded_files(effects, prepared, inputs, source)
    for obj in prepared.plan.objects:
        if obj.action == "copy":
            assert obj.source_path is not None
            identity = source_objects[obj.source_path]
            if isinstance(source, BlobReleaseSource) and isinstance(publisher, SupervisedBlobPublisher):
                bound = source.binding(identity)
                publisher.copy(source.container_client, identity, bound.etag, source_path=bound.name)
            elif isinstance(source, LocalReleaseSource) and isinstance(publisher, SupervisedLocalPublisher):
                publisher.copy(source.root, identity)
            else:
                raise ReleaseInputError("copy route does not match the accepted execution")
    with native_operation(store, job_id, fence, frozen, prepared, inputs, mode="assemble", media=media) as objects:
        for path, output, length, digest in objects:
            publisher.write_from_path(path, output, length=length, sha256=digest)

    frozen_payload = serialize_model(frozen.content)
    publisher.write(FROZEN_INPUT_PATH, [frozen_payload], length=len(frozen_payload), sha256=sha256_hex(frozen_payload))
    preview = ReleasePreview(
        frozen_input_sha256=frozen.sha256,
        episode_count=len(prepared.episodes),
        warnings=prepared.plan.warnings,
        actions=prepared.plan.to_preview_actions(),
        capacity=CapacityEstimate(
            estimated_object_count=len(prepared.plan.objects),
            estimated_output_bytes=sum(obj.estimated_output_bytes for obj in prepared.plan.objects),
        ),
    )
    preview_payload = serialize_model(preview)
    publisher.write(PREVIEW_PATH, [preview_payload], length=len(preview_payload), sha256=sha256_hex(preview_payload))
    lineage_length, lineage_digest, record_count = _lineage_identity(prepared)
    publisher.write_lineage(LINEAGE_PATH, prepared, length=lineage_length, sha256=lineage_digest)

    local_store = publisher.store
    inventory = _inventory(local_store)
    inventory_payload = serialize_model(inventory)
    publisher.write(
        INVENTORY_PATH, [inventory_payload], length=len(inventory_payload), sha256=sha256_hex(inventory_payload)
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
        MANIFEST_PATH, [manifest_payload], length=len(manifest_payload), sha256=sha256_hex(manifest_payload)
    )

    candidate = verify_candidate(local_store, sha256_hex(manifest_payload))
    store.seal(
        job_id,
        fence,
        {
            RECEIPT_PATH: (len(candidate.receipt_bytes), sha256_hex(candidate.receipt_bytes)),
            REPORT_PATH: (len(candidate.report_bytes), sha256_hex(candidate.report_bytes)),
        },
    )
    publisher.publish(candidate.manifest_sha256)
    result = verify_published(publisher.store, candidate.manifest_sha256)
    store.record_io(job_id, fence, uuid4().hex, {"destination_verification_bytes": publisher.destination_read_bytes})
    return result
