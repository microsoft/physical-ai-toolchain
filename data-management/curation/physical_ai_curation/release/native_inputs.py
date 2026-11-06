"""Worker-independent native input identities and condition-bound validation."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from physical_ai_curation.release.canonical import parse_strict_json, sha256_hex
from physical_ai_curation.release.encoding import EncodeOptions
from physical_ai_curation.release.inputs import (
    BlobReleaseSource,
    FrozenReleaseInputs,
    LocalReleaseSource,
    ReleaseInputError,
)
from physical_ai_curation.release.media import MediaWindow, ObjectPlanRequest, SubsetEpisodeInput
from physical_ai_curation.release.models import ProducerIdentity, SourceObject, SourceObjectRecord
from physical_ai_curation.release.range_reader import RangeReadError, read_selected_rows
from physical_ai_curation.release.record_storage import BlobLocation
from physical_ai_curation.release.source_format import (
    V21_EPISODE_STATS_PATH,
    object_path,
    source_version,
    v21_episode_rows,
    v21_tasks_table,
)
from physical_ai_curation.release.store import CHUNK_BYTES, LocalReleaseStore

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


class _IdentitySink(io.RawIOBase):
    def __init__(self) -> None:
        self.digest = hashlib.sha256()
        self.length = 0

    def writable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.length

    def write(self, content: bytes) -> int:
        self.digest.update(content)
        self.length += len(content)
        return len(content)


def table_identity(table: pa.Table) -> str:
    """Hash canonical Arrow batches without building another table-sized byte buffer."""

    def field_type(dtype: pa.DataType) -> pa.DataType:
        if pa.types.is_list(dtype):
            return pa.list_(pa.field("item", field_type(dtype.value_type), nullable=dtype.value_field.nullable))
        if pa.types.is_large_list(dtype):
            return pa.large_list(pa.field("item", field_type(dtype.value_type), nullable=dtype.value_field.nullable))
        if pa.types.is_fixed_size_list(dtype):
            return pa.list_(
                pa.field("item", field_type(dtype.value_type), nullable=dtype.value_field.nullable), dtype.list_size
            )
        if pa.types.is_struct(dtype):
            return pa.struct([pa.field(field.name, field_type(field.type), nullable=field.nullable) for field in dtype])
        return dtype

    schema = pa.schema(
        [pa.field(field.name, field_type(field.type), nullable=field.nullable) for field in table.schema]
    )
    canonical = table.cast(schema).combine_chunks()
    sink = _IdentitySink()
    with pa.ipc.new_stream(sink, canonical.schema) as writer:
        writer.write_table(canonical, max_chunksize=1024)
    return sink.digest.hexdigest()


def _data_path(index: int) -> str:
    return f"data/chunk-{index // 1000:03d}/file-{index % 1000:03d}.parquet"


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
    metadata_rows = 0

    def fetch(identity: SourceObjectRecord) -> bytes:
        if local_store is not None:
            return local_store.read_control_bytes(identity.path, max_bytes=budget - charged)
        assert isinstance(source, BlobReleaseSource)
        return b"".join(source.iter_bytes(identity, chunk_size=CHUNK_BYTES))

    def read(path: str) -> bytes:
        nonlocal charged, verified_bytes
        identity = identities.get(path)
        available = budget - charged
        if isinstance(source, BlobReleaseSource):
            available = min(available, source.metadata_max_bytes)
        if identity is None or identity.length > available:
            raise ReleaseInputError("condition-bound native inputs exceed the memory budget")
        content = fetch(identity)
        charged += len(content)
        if len(content) != identity.length or (
            isinstance(identity, SourceObject) and sha256_hex(content) != identity.sha256
        ):
            raise ReleaseInputError("condition-bound native input changed")
        verified_bytes += len(content)
        return content

    def table(path: str) -> pa.Table:
        nonlocal charged, metadata_rows
        content = read(path)
        metadata_limit = source.metadata_max_bytes if isinstance(source, BlobReleaseSource) else budget
        parquet = pq.ParquetFile(
            pa.BufferReader(content),
            thrift_string_size_limit=metadata_limit,
            thrift_container_size_limit=metadata_limit,
        )
        metadata = parquet.metadata
        metadata_rows += metadata.num_rows
        if isinstance(source, BlobReleaseSource) and metadata_rows > source.metadata_max_rows:
            raise ReleaseInputError("condition-bound native metadata exceeds the row budget")
        decoded_estimate = sum(metadata.row_group(index).total_byte_size for index in range(metadata.num_row_groups))
        if charged + decoded_estimate * 3 > budget:
            raise ReleaseInputError("condition-bound native tables exceed the memory budget")
        result = parquet.read()
        charged += result.nbytes * 3
        if charged > budget:
            raise ReleaseInputError("condition-bound native tables exceed the memory budget")
        return result

    def data_table(path: str, episodes: set[int]) -> pa.Table:
        nonlocal charged, verified_bytes
        if local_store is not None:
            return table(path)
        assert isinstance(source, BlobReleaseSource)
        identity = identities.get(path)
        remaining = budget - charged
        if identity is None or remaining <= 0:
            raise ReleaseInputError("condition-bound native inputs exceed the memory budget")
        try:
            with source.range_file(identity) as file:
                result = read_selected_rows(
                    file,
                    "episode_index",
                    episodes,
                    max_footer_bytes=min(remaining // 4, source.metadata_max_bytes),
                    max_decoded_bytes=remaining // 4,
                )
        except (RangeReadError, OSError, pa.ArrowException) as exc:
            raise ReleaseInputError("condition-bound native rows could not be range-verified") from exc
        verified_bytes += file.transferred
        charged += file.transferred + result.nbytes * 3
        if charged > budget:
            raise ReleaseInputError("condition-bound native tables exceed the memory budget")
        return result

    def native_path(prefix: str, row: Mapping[str, object]) -> str:
        return object_path(info, row, prefix)

    info = parse_strict_json(read(INFO_PATH))
    if not isinstance(info, dict):
        raise ReleaseInputError("native dataset info must be an object")
    legacy = source_version(info) == "v2.1"
    row_limit = source.metadata_max_rows if isinstance(source, BlobReleaseSource) else 10000
    tasks = v21_tasks_table(read, max_rows=row_limit) if legacy else table(TASKS_PATH)
    rows = []
    if legacy:
        rows = v21_episode_rows(info, read, max_rows=row_limit, has_statistics=V21_EPISODE_STATS_PATH in identities)
    else:
        for path in sorted(identities):
            if path.startswith("meta/episodes/") and path.endswith(".parquet"):
                rows.extend(table(path).to_pylist())
    if isinstance(source, BlobReleaseSource) and len(rows) > source.metadata_max_rows:
        raise ReleaseInputError("condition-bound native metadata exceeds the row budget")
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
    resolved = {}
    wanted: dict[str, set[int]] = {}
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
        resolved[episode] = (descriptor, row, path)
        wanted.setdefault(path, set()).add(descriptor.original_episode_index)
    loaded = {path: data_table(path, episodes) for path, episodes in wanted.items()}
    expected_windows = []
    summaries = {}
    selected_tables = {}
    for episode in frozen.content.selected_episode_ids:
        descriptor, row, path = resolved[episode]
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
                if row.get(f"stats/{camera}/{name}") is not None
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
