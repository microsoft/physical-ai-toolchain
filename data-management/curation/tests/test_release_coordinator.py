"""Release generation coordinator refusal before publication effects."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from physical_ai_curation.release.canonical import sha256_hex
from physical_ai_curation.release.composition import prepare_saved_generation
from physical_ai_curation.release.coordinator import GenerationInputs, generation_request_bytes, run_release_generation
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.inputs import (
    BlobReleaseSource,
    FreezeContext,
    LocalReleaseSource,
    ReleaseInputError,
    ReleaseSelection,
    SavedSelection,
    freeze_release_inputs,
)
from physical_ai_curation.release.jobs import JobRequest
from physical_ai_curation.release.media import MediaWindow, ObjectPlanRequest, StorageEndpoint, SubsetEpisodeInput
from physical_ai_curation.release.models import ProducerIdentity, VersionIdentity
from physical_ai_curation.release.native_worker import _source_spec, native_operation, restore_inputs
from physical_ai_curation.release.publication import LocalPublisher
from physical_ai_curation.release.record_storage import BlobLocation
from physical_ai_curation.release.runner import ConfiguredExecution, execute_job
from physical_ai_curation.release.source_associations import LocalSourceAssociations, source_association_binding
from physical_ai_curation.release.store import LocalReleaseStore
from physical_ai_curation.release.verification import verify_published
from physical_ai_curation.saved_input import SavedResource
from test_release_composition import _generation_case
from test_release_contracts import _producer_dict
from test_release_inputs import SavedReader, _context, _selection
from test_release_jobs import (
    ATTEMPT,
    BLOB,
    OWNER,
    FsContainer,
    OffsetClock,
    _ChildBlobPublisher,
    _request,
    _sdk_calls,
    _store,
)
from test_release_media import _encode_source
from test_release_publishers import _association_intent


def _source_root(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "data").mkdir(parents=True)
    (root / "data/frames.jsonl").write_bytes(b'{"frame_index":0}\n')
    (root / "meta").mkdir()
    (root / "meta/info.json").write_bytes(b'{"total_episodes":1}')
    return root


def _inputs(request: object) -> GenerationInputs:
    return GenerationInputs(
        request=request,  # type: ignore[arg-type]
        sources={},
        source_root=Path("."),
        source_info={},
        source_tables={},
        source_tasks=None,  # type: ignore[arg-type]
        camera_statistics=None,
        preserved_dataset_statistics=None,
        encode_options=None,
        producer=None,  # type: ignore[arg-type]
        confirmed_digest="0" * 64,
        scratch_bytes=0,
        output_bytes=0,
    )


def test_given_source_drift_after_freeze_when_generation_then_refused_before_any_effect(tmp_path: Path) -> None:
    root = _source_root(tmp_path)
    source = LocalReleaseSource(root, finalized=True)
    reader = SavedReader()
    context: FreezeContext = _context()
    selection: ReleaseSelection = _selection()
    expected = asyncio.run(freeze_release_inputs(source, reader, selection, context))

    path = root / "data/frames.jsonl"
    before = path.stat()
    path.write_bytes(b'{"frame_index":1}\n')
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    clock = OffsetClock()
    store = _store(tmp_path / "jobs", clock)
    destination = tmp_path / "destination"
    job_id = store.accept(_request(destination)).job_id
    fence = store.claim(job_id, "worker-a")
    _, _, request = _generation_case()

    with pytest.raises(ReleaseInputError, match="changed"):
        asyncio.run(
            run_release_generation(
                store=store,
                job_id=job_id,
                fence=fence,
                destination=destination,
                attempt=ATTEMPT,
                expected=expected,
                source=source,
                reader=reader,
                selection=selection,
                context=context,
                inputs=_inputs(request),
            )
        )

    assert not destination.exists() or not any(destination.rglob("*"))


def test_given_blob_location_when_native_source_spec_then_secret_free_fields(tmp_path: Path) -> None:
    location = BlobLocation(account_url="https://acct.blob.example", container="release", prefix="run-1/")

    assert _source_spec(location) == {
        "kind": "blob",
        "account_url": "https://acct.blob.example",
        "container": "release",
        "prefix": "run-1",
    }
    assert _source_spec(tmp_path)["kind"] == "local"


def test_given_query_bearing_container_url_when_blob_location_then_refused() -> None:
    client = SimpleNamespace(url="https://acct.blob.example/release?sv=signature")

    with pytest.raises(ReleaseInputError, match="query-free https container address"):
        _ = BlobReleaseSource(client).location


def test_given_persisted_request_without_native_buffers_when_restored_then_fresh_guarded_identities(
    tmp_path: Path,
) -> None:
    _source, _reader, _selection, _context, frozen, inputs, _video = _native_case(tmp_path)
    prepared = prepare_saved_generation(
        frozen.content,
        inputs.request,
        inputs.sources,
        encode_options=inputs.encode_options,
        camera_statistics=inputs.camera_statistics,
    )
    store = _store(tmp_path / "jobs", OffsetClock())
    destination = tmp_path / "output"
    request = replace(
        _request(destination), frozen_request=generation_request_bytes(frozen, inputs), reserved_bytes=32 * 1024**2
    )
    job_id = store.accept(request).job_id
    fence = store.claim(job_id, "worker-restore")
    empty = replace(inputs, source_info={}, source_tables={}, source_tasks=pa.table({}))

    restored = restore_inputs(store, job_id, fence, frozen, prepared, empty)

    assert restored.source_info == inputs.source_info
    assert restored.native_table_identities
    assert restored.native_task_identity
    assert restored.source_tables == {}
    assert generation_request_bytes(frozen, restored) == store.frozen_request(job_id)
    with native_operation(store, job_id, fence, frozen, prepared, restored, mode="validate"):
        pass


@pytest.mark.parametrize("processed", [False, True])
@pytest.mark.parametrize("associated", [False, True])
def test_given_accepted_job_when_independent_runner_reopens_then_verified_without_acceptor_buffers(
    tmp_path: Path, processed: bool, associated: bool
) -> None:
    source, reader, selection, context, frozen, inputs, _video = _native_case(tmp_path, processed=processed)
    destination = tmp_path / "output"
    root = tmp_path / "jobs"
    store = _store(root, OffsetClock())
    intent = _association_intent(destination).model_copy(
        update={"attempt": ATTEMPT, "scope_sha256": sha256_hex(OWNER.encode()), "frozen_input_sha256": frozen.sha256}
    )
    job_id = store.accept(
        replace(
            _request(destination),
            frozen_request=generation_request_bytes(frozen, inputs),
            reserved_bytes=32 * 1024**2,
            execution_binding=source_association_binding(intent) if associated else b"",
        )
    ).job_id
    del store, inputs
    reopened = _store(root, OffsetClock())

    result = asyncio.run(
        execute_job(reopened, job_id, ConfiguredExecution(source, reader, selection, context, destination))
    )

    assert result is not None
    assert verify_published(LocalReleaseStore(destination / ATTEMPT.prefix), result.identity.manifest_sha256) == result
    record = reopened.storage.snapshot()[job_id]
    assert record["state"] == "committed"
    assert record["diagnostics"]["validation.succeeded"]["count"] == 1
    assert all(runtime["stopped"] for runtime in record["runtimes"].values())
    if associated:
        from physical_ai_curation.release.record_storage import RecordLimits

        source.root.rename(tmp_path / "source-unavailable")
        associations = LocalSourceAssociations(destination, limits=RecordLimits())
        assert associations.discover(source_key=intent.source_key, scope_sha256=intent.scope_sha256) == [
            destination / ATTEMPT.prefix
        ]


@pytest.mark.parametrize("foreign", [False, True])
def test_given_cancelled_associated_job_when_runner_reopens_then_pending_cleanup_precedes_capacity_release(
    tmp_path: Path, foreign: bool
) -> None:
    from physical_ai_curation.release.record_storage import RecordLimits

    source, reader, selection, context, frozen, inputs, _video = _native_case(tmp_path)
    destination = tmp_path / "output"
    store = _store(tmp_path / "jobs", OffsetClock())
    intent = _association_intent(destination).model_copy(
        update={"attempt": ATTEMPT, "scope_sha256": sha256_hex(OWNER.encode()), "frozen_input_sha256": frozen.sha256}
    )
    job_id = store.accept(
        replace(
            _request(destination),
            frozen_request=generation_request_bytes(frozen, inputs),
            reserved_bytes=32 * 1024**2,
            execution_binding=source_association_binding(intent),
        )
    ).job_id
    associations = LocalSourceAssociations(destination, limits=RecordLimits())
    associations.ensure_intent(intent.model_copy(update={"source_revision": "foreign"}) if foreign else intent)
    store.request_cancel(job_id, scope=_request(destination).scope, principal=OWNER)
    source.root.rename(tmp_path / "removed-source")
    configured = ConfiguredExecution(None, reader, selection, context, destination)

    if foreign:
        with pytest.raises(ReleaseVerificationError):
            asyncio.run(execute_job(store, job_id, configured))
    else:
        assert asyncio.run(execute_job(store, job_id, configured)) is None

    record = store.storage.snapshot()[job_id]
    assert record["state"] == ("cancelling" if foreign else "cancelled")
    assert (record["reservation"]["status"] == "released") is not foreign
    assert (destination / "_release_associations" / f"{ATTEMPT.package_id}.json").exists() is foreign


@pytest.mark.parametrize("missing", [False, True])
def test_given_stale_or_missing_source_when_independent_runner_restores_then_no_destination_effect(
    tmp_path: Path, missing: bool
) -> None:
    source, reader, selection, context, frozen, inputs, _video = _native_case(tmp_path)
    destination = tmp_path / "output"
    store = _store(tmp_path / "jobs", OffsetClock())
    job_id = store.accept(
        replace(
            _request(destination), frozen_request=generation_request_bytes(frozen, inputs), reserved_bytes=32 * 1024**2
        )
    ).job_id
    if not missing:
        (source.root / "meta/info.json").write_bytes(b"{}")

    with pytest.raises(ReleaseInputError):
        asyncio.run(
            execute_job(
                store, job_id, ConfiguredExecution(None if missing else source, reader, selection, context, destination)
            )
        )

    assert not destination.exists()
    assert store.storage.snapshot()[job_id]["diagnostics"]["validation.failed"]["count"] == 1


def test_given_container_url_when_blob_location_then_container_and_https_account() -> None:
    client = SimpleNamespace(url="https://acct.blob.example/release")

    location = BlobReleaseSource(client).location

    assert location.container == "release"
    assert location.account_url.startswith("https://")


def _native_case(tmp_path: Path, *, processed: bool = False) -> tuple:
    root = tmp_path / "native"
    (root / "meta/episodes/chunk-000").mkdir(parents=True)
    (root / "data/chunk-000").mkdir(parents=True)
    camera = "observation.images.front"
    video_path = f"videos/{camera}/chunk-000/file-000.mp4"
    (root / video_path).parent.mkdir(parents=True)
    video = _encode_source([np.full((24, 32, 3), 30, dtype=np.uint8)] * 2)
    (root / video_path).write_bytes(video)
    table = pa.table(
        {
            "episode_index": [0, 0],
            "frame_index": [0, 1],
            "index": [0, 1],
            "timestamp": [0.0, 0.1],
            "task_index": [0, 0],
            "observation.state": [[1.0, 1.0], [2.0, 2.0]],
        }
    )
    pq.write_table(table, root / "data/chunk-000/file-000.parquet")
    tasks = pa.table({"task_index": [0], "task": ["pick"]})
    pq.write_table(tasks, root / "meta/tasks.parquet")
    info = {
        "codebase_version": "v3.0",
        "fps": 10.0,
        "total_episodes": 1,
        "total_frames": 2,
        "features": {
            **{
                name: {"dtype": "int64", "shape": [1], "names": None}
                for name in ("episode_index", "frame_index", "index", "task_index")
            },
            "timestamp": {"dtype": "float64", "shape": [1], "names": None},
            "observation.state": {"dtype": "float64", "shape": [2], "names": ["x", "y"]},
            camera: {
                "dtype": "video",
                "shape": [24, 32, 3],
                "info": {"video.codec": "h264", "video.pix_fmt": "yuv420p"},
            },
        },
    }
    summary = {
        "count": [2],
        **{name: [[[value]]] * 3 for name, value in (("min", 0.1), ("max", 0.2), ("mean", 0.15), ("std", 0.01))},
    }
    row = {
        "episode_index": 0,
        "length": 2,
        "data/chunk_index": 0,
        "data/file_index": 0,
        f"videos/{camera}/chunk_index": 0,
        f"videos/{camera}/file_index": 0,
        f"videos/{camera}/from_timestamp": 0.0,
        f"videos/{camera}/to_timestamp": 0.2,
        **{f"stats/{camera}/{name}": value for name, value in summary.items()},
    }
    cameras = [camera]
    if processed:
        wrist = "observation.images.wrist"
        cameras.append(wrist)
        second = root / video_path.replace(camera, wrist)
        second.parent.mkdir(parents=True)
        second.write_bytes(video)
        info["features"][wrist] = info["features"][camera]
        row.update({name.replace(camera, wrist): value for name, value in list(row.items()) if camera in name})
    (root / "meta/info.json").write_text(json.dumps(info))
    pq.write_table(pa.Table.from_pylist([row]), root / "meta/episodes/chunk-000/file-000.parquet")
    source = LocalReleaseSource(root, finalized=True)
    reader, selection, context = SavedReader(), _selection(), _context()
    if processed:
        reader.edit = SavedResource({"operations": {"globalTransform": {"colorFilter": "invert"}}}, "edit-v1")
        selection = _selection(SavedSelection(kind="edits", episode_index=0, expected_revision="edit-v1"))
    frozen = asyncio.run(freeze_release_inputs(source, reader, selection, context))
    endpoint = StorageEndpoint(alias="local", kind="local", volume="same")
    request = ObjectPlanRequest(
        source=endpoint,
        destination=endpoint,
        scratch=endpoint,
        target_format=VersionIdentity(name="lerobot", version="v3.0"),
        frozen_input_sha256=frozen.sha256,
        selected_episode_ids=frozen.content.selected_episode_ids,
        windows=tuple(
            MediaWindow(
                frozen.content.selected_episode_ids[0],
                name,
                next(item for item in frozen.content.source_objects if item.path == video_path.replace(camera, name)),
                0,
                0.2,
                0.2,
            )
            for name in cameras
        ),
        tool=VersionIdentity(name="pyav-libx264", version="test"),
        source_objects=frozen.content.source_objects,
    )
    episode = frozen.content.selected_episode_ids[0]
    inputs = GenerationInputs(
        request,
        {episode: SubsetEpisodeInput(episode, 0, 2, 10.0)},
        root,
        info,
        {episode: table},
        tasks,
        {episode: {name: summary for name in cameras}},
        None,
        None,
        ProducerIdentity.model_validate(_producer_dict()),
        "",
        8 * 1024**2,
        1024**2,
    )
    prepared = prepare_saved_generation(
        frozen.content, request, inputs.sources, camera_statistics=inputs.camera_statistics
    )
    return source, reader, selection, context, frozen, replace(inputs, confirmed_digest=prepared.plan.digest), video


@pytest.mark.parametrize("processed", [False, True])
def test_given_frozen_native_source_when_planning_then_fixture_equivalent_without_acceptor_tables(
    tmp_path: Path, processed: bool
) -> None:
    from physical_ai_curation.release.coordinator import plan_generation_inputs

    source, _reader, _selection, _context_value, frozen, expected, _video = _native_case(tmp_path, processed=processed)
    planned = plan_generation_inputs(
        source,
        frozen,
        source_endpoint=expected.request.source,
        destination_endpoint=expected.request.destination,
        scratch_endpoint=expected.request.scratch,
        producer=expected.producer,
        tool=expected.request.tool,
        scratch_bytes=expected.scratch_bytes,
        output_bytes=expected.output_bytes,
    )

    assert generation_request_bytes(frozen, planned) == generation_request_bytes(frozen, expected)
    assert planned.source_tables == {}
    assert planned.source_tasks.num_rows == 0
    assert not (tmp_path / "output").exists()


NATIVE_BLOB_CODE = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
from test_release_jobs import fs_factory
from physical_ai_curation.release.native_worker import _process
_process(Path(sys.argv[1]), container_factory=fs_factory(Path(sys.argv[2])))
"""


def _blob_native_case(tmp_path: Path, *, processed: bool = False) -> tuple:
    local, reader, selection, context, _, inputs, _ = _native_case(tmp_path, processed=processed)
    sdk = tmp_path / "sdk"
    remote = FsContainer(sdk, "example", "source")
    for path in local.root.rglob("*"):
        if path.is_file():
            remote.put("native/" + path.relative_to(local.root).as_posix(), path.read_bytes())
    source = BlobReleaseSource(remote, prefix="native")
    frozen = asyncio.run(freeze_release_inputs(source, reader, selection, context))
    identities = {item.path: item for item in frozen.content.source_objects}
    endpoint = StorageEndpoint(alias="blob", kind="blob", account=source.location.account_url)
    request = replace(
        inputs.request,
        source=endpoint,
        destination=endpoint,
        frozen_input_sha256=frozen.sha256,
        source_objects=frozen.content.source_objects,
        windows=tuple(replace(window, source=identities[window.source.path]) for window in inputs.request.windows),
    )
    inputs = replace(inputs, request=request, source_root=source.location)
    prepared = prepare_saved_generation(
        frozen.content, request, inputs.sources, camera_statistics=inputs.camera_statistics
    )
    inputs = replace(inputs, confirmed_digest=prepared.plan.digest)
    local.root.rename(tmp_path / "unavailable-local-source")
    (sdk / "calls.jsonl").unlink()
    return source, reader, selection, context, frozen, inputs, sdk, remote


@pytest.mark.parametrize("mode,mutated", [("validate", False), ("assemble", False), ("validate", True)])
def test_given_blob_native_inputs_when_supervised_then_only_conditioned_tables_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, mutated: bool
) -> None:
    _, _, _, _, frozen, inputs, sdk, remote = _blob_native_case(tmp_path)
    prepared = prepare_saved_generation(
        frozen.content, inputs.request, inputs.sources, camera_statistics=inputs.camera_statistics
    )
    if mutated:
        remote.put("native/meta/info.json", b"{}")
    monkeypatch.setattr(
        "physical_ai_curation.release.native_worker._command",
        lambda path: [sys.executable, "-B", "-c", NATIVE_BLOB_CODE, str(path), str(sdk), str(Path(__file__).parent)],
    )
    store = _store(tmp_path / "ledger", OffsetClock())
    job = store.accept(
        JobRequest(
            "scope-a",
            "owner-a",
            "blob-native",
            ATTEMPT,
            BLOB,
            generation_request_bytes(frozen, inputs),
            16 * 1024**2,
            route="blob",
        )
    )
    fence = store.claim(job.job_id, "worker")

    if mutated:
        with (
            pytest.raises(ReleaseInputError),
            native_operation(store, job.job_id, fence, frozen, prepared, inputs, mode=mode),
        ):
            pytest.fail("stale native metadata must not be accepted")
    else:
        with native_operation(store, job.job_id, fence, frozen, prepared, inputs, mode=mode) as outputs:
            assert bool(outputs) == (mode == "assemble")
            if outputs:
                info = next(path for name, path, _, _ in outputs if name == "meta/info.json")
                assert json.loads(info.read_bytes())["total_frames"] == 2

    downloads = _sdk_calls(sdk, "download_blob")
    assert not any("/videos/" in call["name"] for call in downloads)
    assert _sdk_calls(sdk, "upload_blob") == []
    record = store.storage.snapshot()[job.job_id]
    assert record["effects"] == {}
    assert record["runtimes"] and all(runtime["stopped"] for runtime in record["runtimes"].values())
    assert record["scratch_bytes"] == 0


@pytest.mark.parametrize("processed", [False, True])
def test_given_blob_source_when_coordinated_then_independent_native_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, processed: bool
) -> None:
    source, reader, selection, context, frozen, inputs, sdk, remote = _blob_native_case(tmp_path, processed=processed)
    monkeypatch.setattr(
        "physical_ai_curation.release.native_worker._command",
        lambda path: [sys.executable, "-B", "-c", NATIVE_BLOB_CODE, str(path), str(sdk), str(Path(__file__).parent)],
    )
    store = _store(tmp_path / "ledger", OffsetClock())
    job = store.accept(
        JobRequest(
            "scope-a",
            "owner-a",
            "blob-native",
            ATTEMPT,
            BLOB,
            generation_request_bytes(frozen, inputs),
            16 * 1024**2,
            route="blob",
        )
    )
    fence = store.claim(job.job_id, "worker")
    publisher = _ChildBlobPublisher(
        FsContainer(sdk, "example", "release"), BLOB, ATTEMPT, store, job.job_id, fence, sdk_root=sdk
    )

    result = asyncio.run(
        run_release_generation(
            store=store,
            job_id=job.job_id,
            fence=fence,
            destination=BLOB,
            attempt=ATTEMPT,
            expected=frozen,
            source=source,
            reader=reader,
            selection=selection,
            context=context,
            inputs=inputs,
            publisher=publisher,
        )
    )
    for item in list(remote.list_blobs(name_starts_with="native/")):
        remote.remove(item.name)

    assert verify_published(publisher.store, result.identity.manifest_sha256) == result
    info = json.loads(publisher.store.read_control_bytes("meta/info.json", max_bytes=1024**2))
    assert info["total_frames"] == 2
    record = store.storage.snapshot()[job.job_id]
    assert record["state"] == "committed"
    if processed:
        import av

        assert record["io_counters"].get("storage_copy_bytes", 0) == 0
        for index, window in enumerate(inputs.request.windows):
            output = tmp_path / f"readback-{index}.mp4"
            output.write_bytes(b"".join(publisher.store.iter_bytes(window.source.path, chunk_size=1024**2)))
            with av.open(str(output)) as media:
                frames = [frame.to_ndarray(format="rgb24") for frame in media.decode(video=0)]
            assert len(frames) == 2
            assert np.mean(frames[0]) == pytest.approx(225, abs=6)
        assert record["io_counters"]["affected_download_bytes"] > 0
    else:
        assert record["io_counters"]["storage_copy_bytes"] > 0
    assert record["io_counters"]["source_verification_bytes"] > 0
    assert record["io_counters"]["destination_verification_bytes"] > 0
    assert record["scratch_bytes"] == 0
    assert all(runtime["stopped"] for runtime in record["runtimes"].values())


def test_given_copy_only_source_when_coordinated_then_independent_marked_release(tmp_path: Path) -> None:
    source, reader, selection, context, frozen, inputs, video = _native_case(tmp_path)
    store = _store(tmp_path / "ledger", OffsetClock())
    destination = tmp_path / "destination"
    job = store.accept(
        JobRequest(
            "scope-a", "owner-a", "copy", ATTEMPT, destination, generation_request_bytes(frozen, inputs), 16 * 1024**2
        )
    )
    fence = store.claim(job.job_id, "worker")
    with (
        patch("av.open", side_effect=AssertionError("exact copies must not decode")),
        patch(
            "physical_ai_curation.release.coordinator.pq.read_table",
            side_effect=AssertionError("native reads require supervised child"),
        ),
        patch(
            "physical_ai_curation.release.tables.generate_native_selection",
            side_effect=AssertionError("native assembly requires supervised child"),
        ),
    ):
        result = asyncio.run(
            run_release_generation(
                store=store,
                job_id=job.job_id,
                fence=fence,
                destination=destination,
                attempt=ATTEMPT,
                expected=frozen,
                source=source,
                reader=reader,
                selection=selection,
                context=context,
                inputs=inputs,
            )
        )
    source.root.rename(tmp_path / "removed-source")
    root = destination / ATTEMPT.prefix
    assert verify_published(LocalReleaseStore(root)).identity == result.identity
    assert sha256_hex((root / inputs.request.windows[0].source.path).read_bytes()) == sha256_hex(video)
    status = store.get(job.job_id, scope="scope-a", principal="owner-a")
    assert status.state == "committed"
    assert status.io_counters["storage_copy_bytes"] == len(video)
    assert status.io_counters["source_verification_bytes"] > len(video)
    assert status.io_counters["destination_verification_bytes"] > sum(
        item.length for item in LocalReleaseStore(root).list_objects()
    )
    assert status.io_counters["generated_output_bytes"] > 0
    assert 0 < status.peak_scratch_bytes < inputs.scratch_bytes
    assert status.observed_scratch_bytes == status.scratch_bytes == 0
    assert LocalPublisher(destination, ATTEMPT, None).discover() == [root]


def test_given_unbound_native_table_when_generation_then_refused_before_effects(tmp_path: Path) -> None:
    source, reader, selection, context, frozen, inputs, _ = _native_case(tmp_path)
    episode = frozen.content.selected_episode_ids[0]
    altered = inputs.source_tables[episode].set_column(5, "observation.state", pa.array([[999.0], [999.0]]))
    inputs = replace(inputs, source_tables={episode: altered})
    store = _store(tmp_path / "ledger", OffsetClock())
    destination = tmp_path / "destination"
    job = store.accept(
        JobRequest(
            "scope-a",
            "owner-a",
            "altered",
            ATTEMPT,
            destination,
            generation_request_bytes(frozen, inputs),
            16 * 1024**2,
        )
    )
    fence = store.claim(job.job_id, "worker")
    with pytest.raises(ReleaseInputError, match="condition-bound"):
        asyncio.run(
            run_release_generation(
                store=store,
                job_id=job.job_id,
                fence=fence,
                destination=destination,
                attempt=ATTEMPT,
                expected=frozen,
                source=source,
                reader=reader,
                selection=selection,
                context=context,
                inputs=inputs,
            )
        )
    assert not destination.exists()


def test_given_two_processed_cameras_when_coordinated_then_sequential_native_publication(tmp_path: Path) -> None:
    import av

    source, reader, selection, context, frozen, inputs, _ = _native_case(tmp_path, processed=True)
    store = _store(tmp_path / "ledger", OffsetClock())
    destination = tmp_path / "destination"
    job = store.accept(
        JobRequest(
            "scope-a",
            "owner-a",
            "processed",
            ATTEMPT,
            destination,
            generation_request_bytes(frozen, inputs),
            16 * 1024**2,
        )
    )
    fence = store.claim(job.job_id, "worker")
    result = asyncio.run(
        run_release_generation(
            store=store,
            job_id=job.job_id,
            fence=fence,
            destination=destination,
            attempt=ATTEMPT,
            expected=frozen,
            source=source,
            reader=reader,
            selection=selection,
            context=context,
            inputs=inputs,
        )
    )
    source.root.rename(tmp_path / "removed-source")
    root = destination / ATTEMPT.prefix
    assert verify_published(LocalReleaseStore(root)).identity == result.identity
    for window in inputs.request.windows:
        with av.open(str(root / window.source.path)) as video:
            frames = [frame.to_ndarray(format="rgb24") for frame in video.decode(video=0)]
        assert len(frames) == 2
        assert np.mean(frames[0]) == pytest.approx(225, abs=6)
    assert store.get(job.job_id, scope="scope-a", principal="owner-a").effects_stopped
    assert not list((store.root / "scratch").rglob("encoded.mp4"))


def test_given_changed_execution_settings_when_request_accepted_then_refused_before_effects(tmp_path: Path) -> None:
    source, reader, selection, context, frozen, inputs, _ = _native_case(tmp_path)
    store = _store(tmp_path / "ledger", OffsetClock())
    destination = tmp_path / "destination"
    job = store.accept(
        JobRequest(
            "scope-a",
            "owner-a",
            "bound",
            ATTEMPT,
            destination,
            generation_request_bytes(frozen, inputs),
            16 * 1024**2,
        )
    )
    fence = store.claim(job.job_id, "worker")
    changed = replace(inputs, output_bytes=inputs.output_bytes * 2)
    with pytest.raises(ReleaseInputError, match="durably accepted"):
        asyncio.run(
            run_release_generation(
                store=store,
                job_id=job.job_id,
                fence=fence,
                destination=destination,
                attempt=ATTEMPT,
                expected=frozen,
                source=source,
                reader=reader,
                selection=selection,
                context=context,
                inputs=changed,
            )
        )
    assert not destination.exists()
