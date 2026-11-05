"""Private bounded native validation/assembly protocol under the release guardian."""

from __future__ import annotations

import hashlib
import io
import json
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import TypeAdapter

from physical_ai_curation.release.canonical import canonical_json_bytes, normalize_release_path
from physical_ai_curation.release.composition import NativeGenerationPlan
from physical_ai_curation.release.encoding import VideoOutput
from physical_ai_curation.release.execution import MAX_RESULT_BYTES, MAX_SPEC_BYTES, BoundedSink
from physical_ai_curation.release.inputs import (
    BlobReleaseSource,
    FrozenReleaseInputs,
    LocalReleaseSource,
    ReleaseInputError,
)
from physical_ai_curation.release.jobs import CapacityError, ReleaseJobStore
from physical_ai_curation.release.media import ObjectPlanRequest, ReleasePlanError, SubsetEpisodeInput
from physical_ai_curation.release.models import ProducerIdentity
from physical_ai_curation.release.record_storage import BlobLocation
from physical_ai_curation.release.worker import run_supervised

if TYPE_CHECKING:
    from physical_ai_curation.release.coordinator import GenerationInputs


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


def _source_spec(source_root: Path | BlobLocation) -> dict[str, str]:
    """Serialize only non-secret source identity for the child process."""
    if isinstance(source_root, BlobLocation):
        return {
            "kind": "blob",
            "account_url": source_root.account_url,
            "container": source_root.container,
            "prefix": source_root.prefix,
        }
    return {"kind": "local", "root": str(source_root.resolve())}


def _command(request: Path) -> list[str]:
    """Build the child command; tests may replace it to inject a fake container transport."""
    return [sys.executable, "-B", "-m", "physical_ai_curation.release.native_worker", str(request)]


def _entra_container_factory(stack: ExitStack, timeout: float) -> Callable[[str, str], Any]:
    """Open Entra-authenticated container clients that close with the child's stack."""
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import ContainerClient

    credential = stack.enter_context(DefaultAzureCredential())

    def build(account_url: str, name: str) -> Any:
        return stack.enter_context(
            ContainerClient(
                account_url,
                name,
                credential=credential,
                connection_timeout=timeout,
                read_timeout=timeout,
                retry_total=0,
            )
        )

    return build


@contextmanager
def native_operation(
    store: ReleaseJobStore,
    job_id: str,
    fence: int,
    frozen: FrozenReleaseInputs,
    prepared: NativeGenerationPlan,
    inputs: GenerationInputs,
    *,
    mode: Literal["validate", "assemble", "restore"],
    media: Mapping[str, VideoOutput] | None = None,
    restored: dict[str, Any] | None = None,
) -> Iterator[list[tuple[str, Path, int, str]]]:
    """Run native decoding/assembly in a capped child and expose only stopped verified output."""
    if sum(table.nbytes for table in inputs.source_tables.values()) * 3 > store.settings.memory_bytes:
        raise CapacityError("native inputs exceed the admitted memory bound")
    spec = {
        "version": 1,
        "mode": mode,
        "frozen": TypeAdapter(FrozenReleaseInputs).dump_python(frozen, mode="json"),
        "prepared": TypeAdapter(NativeGenerationPlan).dump_python(prepared, mode="json"),
        "request": TypeAdapter(ObjectPlanRequest).dump_python(inputs.request, mode="json"),
        "sources": TypeAdapter(dict[str, SubsetEpisodeInput]).dump_python(dict(inputs.sources), mode="json"),
        "source": _source_spec(inputs.source_root),
        "source_info": inputs.source_info,
        "tables": inputs.native_table_identities
        or {episode: table_identity(table) for episode, table in inputs.source_tables.items()},
        "tasks": inputs.native_task_identity or table_identity(inputs.source_tasks),
        "camera_statistics": inputs.camera_statistics,
        "dataset_statistics": inputs.preserved_dataset_statistics,
        "producer": inputs.producer.model_dump(mode="json"),
        "confirmed_digest": inputs.confirmed_digest,
        "memory_bytes": min(store.settings.memory_bytes, inputs.request.limits.processing_memory_bytes),
        "recovery_seconds": store.settings.recovery_seconds,
        "scratch_bytes": inputs.scratch_bytes,
        "media": TypeAdapter(dict[str, VideoOutput]).dump_python(dict(media or {}), mode="json"),
    }
    content = json.dumps(spec, separators=(",", ":"), allow_nan=False).encode()
    if len(content) > MAX_SPEC_BYTES or inputs.scratch_bytes < len(content) + MAX_RESULT_BYTES:
        raise CapacityError("native request exceeds its admitted scratch bound")
    directory = store.create_scratch_workspace(job_id, fence, inputs.scratch_bytes)
    try:
        request = directory / "request.json"
        request.write_bytes(content)
        outcome = run_supervised(
            store,
            job_id,
            fence,
            _command(request),
            memory_bytes=store.settings.memory_bytes,
            file_bytes=inputs.scratch_bytes,
            heartbeat_seconds=min(5.0, store.settings.lease_seconds / 4),
            cwd=directory,
            grace_seconds=store.settings.grace_seconds,
            liveness_seconds=store.settings.guardian_seconds,
        )
        if outcome.reason != "exited" or outcome.returncode != 0:
            raise ReleaseInputError("supervised condition-bound native processing did not complete")
        result_path = directory / "result.json"
        if result_path.stat().st_size > MAX_RESULT_BYTES:
            raise CapacityError("native result exceeds its protocol bound")
        result = json.loads(result_path.read_bytes())
        if result["state"] != "complete":
            raise ReleaseInputError("native inputs are not condition-bound or exceed supported processing bounds")
        if restored is not None:
            restored.update(result.get("restored", {}))
        counter = (
            "affected_download_bytes" if isinstance(inputs.source_root, BlobLocation) else "source_verification_bytes"
        )
        store.record_io(job_id, fence, directory.name, {counter: result["source_verified_bytes"]})
        outputs = []
        observed = len(content) + result_path.stat().st_size
        for item in result["objects"]:
            path = normalize_release_path(item["path"])
            output = directory / "objects" / path
            observed += output.stat().st_size
            if observed > inputs.scratch_bytes or output.stat().st_size != item["length"]:
                raise CapacityError("native output exceeds its admitted scratch bound")
            outputs.append((path, output, item["length"], item["sha256"]))
        store.record_scratch_usage(job_id, fence, observed)
        yield outputs
    finally:
        store.cleanup_scratch_workspace(job_id, fence)
        store.release_scratch(job_id, fence)


def restore_inputs(
    store: ReleaseJobStore,
    job_id: str,
    fence: int,
    frozen: FrozenReleaseInputs,
    prepared: NativeGenerationPlan,
    inputs: GenerationInputs,
) -> GenerationInputs:
    """Rebuild only frozen metadata/table identities in a stopped, bounded native child."""
    restored = {}
    with native_operation(store, job_id, fence, frozen, prepared, inputs, mode="restore", restored=restored):
        pass
    if set(restored) != {"source_info", "tables", "tasks"}:
        raise ReleaseInputError("native restoration result is incomplete")
    return replace(
        inputs,
        source_info=restored["source_info"],
        native_table_identities=restored["tables"],
        native_task_identity=restored["tasks"],
    )


def _process(request: Path, *, container_factory: Callable[[str, str], Any] | None = None) -> None:
    from physical_ai_curation.release.coordinator import (
        EPISODES_PATH,
        INFO_PATH,
        STATS_PATH,
        TASKS_PATH,
        GenerationInputs,
        _data_path,
        _validate_native_inputs,
    )
    from physical_ai_curation.release.tables import generate_native_selection

    if request.stat().st_size > MAX_SPEC_BYTES:
        raise CapacityError("native request exceeds its protocol bound")
    spec = json.loads(request.read_bytes())
    if spec["version"] != 1 or spec["mode"] not in ("validate", "assemble", "restore"):
        raise ReleaseInputError("unsupported native protocol")
    frozen = TypeAdapter(FrozenReleaseInputs).validate_python(spec["frozen"])
    prepared = TypeAdapter(NativeGenerationPlan).validate_python(spec["prepared"])
    source_spec = spec["source"]
    with ExitStack() as stack:
        if source_spec["kind"] == "local":
            source_location = Path(source_spec["root"])
            source = LocalReleaseSource(source_location, finalized=True)
        elif source_spec["kind"] == "blob":
            source_location = BlobLocation(source_spec["account_url"], source_spec["container"], source_spec["prefix"])
            factory = (
                container_factory
                if container_factory is not None
                else _entra_container_factory(stack, spec["recovery_seconds"])
            )
            source = BlobReleaseSource(
                factory(source_location.account_url, source_location.container), prefix=source_location.prefix
            )
        else:
            raise ReleaseInputError("unsupported native source")
        inputs = GenerationInputs(
            request=TypeAdapter(ObjectPlanRequest).validate_python(spec["request"]),
            sources=TypeAdapter(dict[str, SubsetEpisodeInput]).validate_python(spec["sources"]),
            source_root=source_location,
            source_info=spec["source_info"],
            source_tables={},
            source_tasks=pa.table({}),
            camera_statistics=spec["camera_statistics"],
            preserved_dataset_statistics=spec["dataset_statistics"],
            encode_options=prepared.encode_options,
            producer=ProducerIdentity.model_validate(spec["producer"]),
            confirmed_digest=spec["confirmed_digest"],
            scratch_bytes=spec["scratch_bytes"],
            output_bytes=0,
        )
        bound = _validate_native_inputs(
            source,
            frozen,
            inputs,
            spec["memory_bytes"] // 2,
            table_identities=spec["tables"],
            task_identity=spec["tasks"],
            restore=spec["mode"] == "restore" or (spec["mode"] == "assemble" and not spec["source_info"]),
        )
    objects = []
    remaining = spec["scratch_bytes"] - request.stat().st_size - MAX_RESULT_BYTES

    def write(path: str, table: pa.Table | None = None, content: bytes | None = None) -> None:
        nonlocal remaining
        output = request.parent / "objects" / path
        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with output.open("x+b") as stream:
            sink = BoundedSink(stream, remaining)
            if table is not None:
                pq.write_table(table, sink)
            elif content is not None:
                sink.write(content)
        length = output.stat().st_size
        remaining -= length
        digest = hashlib.sha256()
        with output.open("rb") as stream:
            while chunk := stream.read(1024**2):
                digest.update(chunk)
        objects.append({"path": path, "length": length, "sha256": digest.hexdigest()})

    if spec["mode"] == "assemble":
        result = generate_native_selection(
            prepared,
            bound.source_info,
            bound.source_tables,
            bound.source_tasks,
            TypeAdapter(dict[str, VideoOutput]).validate_python(spec["media"]),
            confirmed_digest=inputs.confirmed_digest,
            preserved_dataset_statistics=inputs.preserved_dataset_statistics,
            limits=inputs.request.limits,
        )
        for index, table in enumerate(result.data_tables):
            write(_data_path(index), table=table)
        write(EPISODES_PATH, table=result.metadata.episodes)
        write(TASKS_PATH, table=result.tasks)
        write(INFO_PATH, content=canonical_json_bytes(result.metadata.info))
        write(STATS_PATH, content=canonical_json_bytes(result.metadata.statistics))
    restored = (
        {}
        if spec["mode"] != "restore"
        else {
            "source_info": bound.source_info,
            "tables": {episode: table_identity(table) for episode, table in bound.source_tables.items()},
            "tasks": table_identity(bound.source_tasks),
        }
    )
    content = json.dumps(
        {
            "state": "complete",
            "objects": objects,
            "source_verified_bytes": bound.native_verified_bytes,
            "restored": restored,
        },
        separators=(",", ":"),
    ).encode()
    if len(content) > MAX_RESULT_BYTES:
        raise CapacityError("native result exceeds its protocol bound")
    (request.parent / "result.json").write_bytes(content)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        return 2
    request = Path(arguments[0])
    try:
        _process(request)
    except (ReleaseInputError, ReleasePlanError, CapacityError):
        (request.parent / "result.json").write_bytes(b'{"state":"invalid_input","objects":[]}')
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
