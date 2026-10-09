"""Tests for the VLM-as-judge router.

These tests run the full FastAPI app with the echo backend so no GPU or
network is required. They drive the real ``evaluation.vlm_judge`` package
end-to-end (frame extraction, agent, cache) against a synthetic LeRobot v2.1
dataset generated on the fly, so they are self-contained and run in CI without
any pre-downloaded dataset.
"""

from __future__ import annotations

import importlib
import json
import os
from collections.abc import Iterator
from pathlib import Path

import av
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

DATASET_ID = "synthetic-eval"
INSTRUCTION = "Pick up the cube"


def test_given_judge_cli_when_detached_then_same_job_resumes_with_saved_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from evaluation.vlm_judge import run as judge_run

    dataset = tmp_path / DATASET_ID
    _build_dataset(dataset, instruction=INSTRUCTION)
    output = tmp_path / "results.jsonl"
    arguments = [
        "--dataset",
        str(dataset),
        "--dataset-id",
        DATASET_ID,
        "--output",
        str(output),
        "--job-dir",
        str(tmp_path / "jobs"),
        "--backend",
        "echo",
        "--n-frames",
        "4",
        "--cache-dir",
        "",
    ]
    assert judge_run.main([*arguments, "--single", "--detach", "--request-id", "detached"]) == 0
    accepted = json.loads(capsys.readouterr().out)
    assert accepted["status"] == "queued"
    assert not output.exists()
    assert judge_run.main([*arguments, "--operation", "status", "--job-id", accepted["id"]]) == 0
    assert json.loads(capsys.readouterr().out)["judged"] == 0
    assert judge_run.main([*arguments, "--single", "--request-id", "detached"]) == 0
    completed = json.loads(capsys.readouterr().out)
    assert completed["id"] == accepted["id"]
    assert completed["status"] == "succeeded"
    assert json.loads(output.read_text())["instruction"] == INSTRUCTION


def test_given_explicit_sample_author_when_cli_submits_then_ambiguous_global_author_is_not_used(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import asyncio

    from evaluation.vlm_judge import run as judge_run
    from evaluation.vlm_judge.curation import ContributionLedger
    from evaluation.vlm_judge.saved_input import LocalDatasetResolver

    dataset = tmp_path / DATASET_ID
    _build_dataset(dataset, instruction=INSTRUCTION)
    annotations = []
    provenance = {}
    for author in ("first", "selected"):
        instruction = f"Saved instruction from {author}"
        annotations.append(
            {
                "annotator_id": author,
                "language_instruction": {"instruction": instruction, "source": "human"},
                "task_completeness": {"rating": "success"},
            }
        )
        ledger = ContributionLedger()
        ledger.record_changes(
            {},
            {"language_instruction/instruction": instruction, "task_completeness/rating": "success"},
            author_id=author,
            origin="human",
        )
        provenance[author] = ledger.model_dump(mode="json")
    path = dataset / "annotations" / "episodes" / "episode_000000.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"dataset_id": DATASET_ID, "episode_index": 0, "annotations": annotations, "provenance": provenance})
    )
    resolver = LocalDatasetResolver({DATASET_ID: dataset})
    _, snapshot = asyncio.run(
        resolver.resolve(DATASET_ID, 0, principal_scope_id="local", annotation_author_id="selected")
    )
    references = tmp_path / "samples.json"
    references.write_text(
        json.dumps(
            {
                "0": {
                    "annotation_author_id": "selected",
                    "annotation_revision": snapshot.annotation_revision,
                    "snapshot_id": snapshot.snapshot_id,
                }
            }
        )
    )
    output = tmp_path / "results.jsonl"

    code = judge_run.main(
        [
            "--dataset",
            str(dataset),
            "--output",
            str(output),
            "--backend",
            "echo",
            "--mode",
            "sample",
            "--sample-references",
            str(references),
            "--detach",
            "--job-dir",
            str(tmp_path / "jobs"),
        ]
    )

    assert code == 0
    accepted = json.loads(capsys.readouterr().out)
    assert accepted["status"] == "queued"
    declaration = json.loads(output.with_suffix(".jsonl.config.json").read_text())
    assert declaration["saved_inputs"][0]["annotation_author_id"] == "selected"


def test_viewer_durable_job_publishes_canonical_evidence_without_labels(
    tmp_path: Path,
    restore_default_app: pytest.MonkeyPatch,
) -> None:
    import asyncio

    _build_dataset(tmp_path / DATASET_ID, instruction=INSTRUCTION)
    client = _reload_app(restore_default_app, tmp_path)
    response = client.post(
        f"/api/datasets/{DATASET_ID}/episodes/0/judge", json={}, headers={"Idempotency-Key": "durable-request"}
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    assert asyncio.run(client.app.state.judge_jobs.run_once())
    completed = client.get(response.headers["Location"])
    assert completed.json()["status"] == "succeeded", completed.text
    assert completed.json()["judged"] == 1
    assert completed.json()["applied"] == 0
    evidence = client.get("/api/judge/results", params={"dataset_id": DATASET_ID, "episode_index": 0})
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()["items"][0]["result"]["instruction"] == INSTRUCTION
    assert evidence.json()["items"][0]["applicability"] == "current"
    assert not (tmp_path / DATASET_ID / "meta" / "episode_labels.json").exists()
    apply_path = f"/api/judge/jobs/{response.json()['id']}/apply"
    browser_body = json.dumps({"episode_indices": [0]})
    undeclared = client.post(apply_path, content=browser_body, headers={"Content-Type": "text/plain;charset=UTF-8"})
    assert undeclared.status_code == 422
    assert "model_attributes_type" in undeclared.text
    assert not (tmp_path / DATASET_ID / "meta" / "episode_labels.json").exists()
    application = client.post(apply_path, content=browser_body, headers={"Content-Type": "application/json"})
    assert application.status_code == 202, application.text
    assert asyncio.run(client.app.state.judge_jobs.run_once())
    applied = client.get(response.headers["Location"]).json()
    assert applied["applied"] == 1
    assert applied["judged"] == 1
    reloaded = client.get("/api/judge/results", params={"dataset_id": DATASET_ID, "episode_index": 0}).json()
    assert [item["run_id"] for item in reloaded["items"]] == [response.json()["id"]]
    assert reloaded["items"][0]["result_id"] == evidence.json()["items"][0]["result_id"]
    assert client.get(f"/api/datasets/{DATASET_ID}/labels").json()["episodes"]["0"] == ["SUCCESS"]
    saved = json.loads((tmp_path / DATASET_ID / "meta" / "episode_labels.json").read_text())
    assert saved["episodes"]["0"] == ["SUCCESS"]
    assert saved["analysis"] == {}
    assert all(
        item["machine"]["run_id"] == response.json()["id"]
        for item in saved["provenance"]["0"]["contributions"]
        if item["origin"] == "machine"
    )


def test_viewer_recovers_accepted_job_on_startup_without_browser(
    tmp_path: Path,
    restore_default_app: pytest.MonkeyPatch,
) -> None:
    import asyncio

    _build_dataset(tmp_path / DATASET_ID, instruction=INSTRUCTION)
    submitting = _reload_app(restore_default_app, tmp_path)
    response = submitting.post(
        f"/api/datasets/{DATASET_ID}/episodes/0/judge", json={}, headers={"Idempotency-Key": "restart-request"}
    )
    assert response.status_code == 202
    recovered = _reload_app(restore_default_app, tmp_path)

    async def recover() -> None:
        async with recovered.app.router.lifespan_context(recovered.app):
            jobs = recovered.app.state.judge_jobs
            original = submitting.app.state.judge_jobs
            job = next(iter((await original.store.read())["jobs"].values()))
            async with asyncio.timeout(10):
                while (await jobs.get(job["id"], job["actor"]))["status"] not in {"succeeded", "failed"}:
                    await asyncio.sleep(0.01)
            assert (await jobs.get(job["id"], job["actor"]))["status"] == "succeeded"

    asyncio.run(recover())


def test_configured_standalone_api_resolves_saved_inputs_without_caller_paths(tmp_path: Path) -> None:
    import asyncio

    from evaluation.vlm_judge.api import build_router
    from evaluation.vlm_judge.job_storage import LocalJobStore
    from evaluation.vlm_judge.saved_input import LocalDatasetResolver
    from evaluation.vlm_judge.service import BackendConfig, FrameConfig, JudgeService, ServiceConfig
    from fastapi import FastAPI

    _build_dataset(tmp_path, instruction=INSTRUCTION)
    service = JudgeService(ServiceConfig(backend=BackendConfig(kind="echo"), frames=FrameConfig(n_frames=4)))
    app = FastAPI()
    app.include_router(
        build_router(
            service,
            resolver=LocalDatasetResolver({DATASET_ID: tmp_path}),
            local_actor="local-test",
            job_store=LocalJobStore(tmp_path / "jobs"),
        )
    )
    with TestClient(app) as client:
        result = client.post("/judge", json={"dataset_id": DATASET_ID, "episode_index": 0})
        rejected = client.post(
            "/judge",
            json={"dataset_id": DATASET_ID, "episode_index": 0, "video_paths": {"front": "/unconfigured/path"}},
        )
        assert result.status_code == 202, result.text

        async def completed() -> dict[str, object]:
            async with asyncio.timeout(10):
                while True:
                    job = await app.state.judge_jobs.get(result.json()["id"], "local-test")
                    if job["status"] in {"succeeded", "failed"}:
                        return job
                    await asyncio.sleep(0.01)

        saved = client.portal.call(completed)
        assert saved["status"] == "succeeded", saved
        assert saved["targets"][0]["result"]["instruction"] == INSTRUCTION
    assert rejected.status_code == 422


def test_judge_inventory_uses_real_sparse_ids_beyond_ui_cap(
    tmp_path: Path,
    restore_default_app: pytest.MonkeyPatch,
) -> None:
    _build_dataset(tmp_path / DATASET_ID, instruction=INSTRUCTION)
    metadata = tmp_path / DATASET_ID / "meta" / "episodes.jsonl"
    episode = json.loads(metadata.read_text())
    episode["episode_index"] = 1005
    metadata.write_text(json.dumps(episode) + "\n")
    client = _reload_app(restore_default_app, tmp_path)

    inventory = client.get("/api/judge/episodes", params={"dataset_id": DATASET_ID, "limit": 1})

    assert inventory.status_code == 200, inventory.text
    assert inventory.json()["items"] == [1005]
    assert inventory.json()["total"] == 1
    assert (
        client.get("/api/judge/episodes", params={"dataset_id": DATASET_ID, "snapshot_id": "stale"}).status_code == 409
    )


def test_invalid_cached_result_is_not_returned_as_evidence(
    tmp_path: Path, restore_default_app: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evaluation.vlm_judge.cache import JudgeCache

    _build_dataset(tmp_path / DATASET_ID, instruction=INSTRUCTION)
    client = _reload_app(restore_default_app, tmp_path)
    monkeypatch.setattr(JudgeCache, "get", lambda *args: {})

    response = client.get(f"/api/datasets/{DATASET_ID}/episodes/0/judge")

    assert response.status_code == 502


def test_judge_extracts_each_view_with_its_own_window(monkeypatch: pytest.MonkeyPatch) -> None:
    from evaluation.vlm_judge import service as judge_module

    windows = []

    def extract(window: object, **kwargs: object) -> list[object]:
        windows.append(window)
        return []

    monkeypatch.setattr(judge_module, "extract_frames", extract)
    monkeypatch.setattr(judge_module, "tile_horizontally", lambda frames: frames)
    judge_module.JudgeService()._extract(
        video_paths={"front": Path("front.mp4"), "wrist": Path("wrist.mp4")},
        from_s=None,
        to_s=None,
        video_windows={"front": (2.0, 3.0), "wrist": (5.0, 6.0)},
    )
    assert [(window.from_s, window.to_s) for window in windows] == [(2.0, 3.0), (5.0, 6.0)]


def test_remote_judge_cache_uses_source_versions_not_scratch_paths(tmp_path: Path) -> None:
    from evaluation.vlm_judge.cache import JudgeCache

    cache = JudgeCache(tmp_path)
    options = {"instruction": INSTRUCTION, "judge_model": "echo", "prompt_version": "test"}
    first = cache.key(video_paths={"front": Path("first.mp4")}, media_identity={"front": "source-one"}, **options)
    assert first == cache.key(
        video_paths={"front": Path("second.mp4")}, media_identity={"front": "source-one"}, **options
    )
    assert first != cache.key(
        video_paths={"front": Path("first.mp4")}, media_identity={"front": "source-two"}, **options
    )


def test_v3_metadata_preserves_different_camera_windows(tmp_path: Path) -> None:
    from evaluation.vlm_judge.dataset import iter_episodes

    _build_dataset(tmp_path, instruction=INSTRUCTION)
    info_path = tmp_path / "meta" / "info.json"
    info = json.loads(info_path.read_text())
    info.update(
        codebase_version="v3.0", video_path="videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
    )
    info["features"]["obs.wrist"] = info["features"]["obs.front"]
    info_path.write_text(json.dumps(info))
    (tmp_path / "meta" / "episodes.jsonl").unlink()
    metadata = tmp_path / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    metadata.parent.mkdir(parents=True)
    row = {"episode_index": [0], "length": [12], "tasks": [[INSTRUCTION]]}
    for camera, start in (("obs.front", 2.0), ("obs.wrist", 5.0)):
        row.update(
            {
                f"videos/{camera}/chunk_index": [0],
                f"videos/{camera}/file_index": [0],
                f"videos/{camera}/from_timestamp": [start],
                f"videos/{camera}/to_timestamp": [start + 0.4],
            }
        )
    pq.write_table(pa.table(row), metadata)
    record = next(iter_episodes(tmp_path))
    assert record.video_windows == {"obs.front": (2.0, 2.4), "obs.wrist": (5.0, 5.4)}


def test_cold_blob_judge_downloads_only_selected_media_and_reuses_cache(
    tmp_path: Path,
    restore_default_app: pytest.MonkeyPatch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from src.api.services.dataset_service import DatasetService, get_dataset_service
    from src.api.storage.blob_dataset import BlobDatasetProvider

    remote = tmp_path / "remote" / DATASET_ID
    _build_dataset(remote, instruction=INSTRUCTION)
    info_path = remote / "meta" / "info.json"
    info = json.loads(info_path.read_text())
    info["features"]["obs.wrist"] = info["features"]["obs.front"]
    info_path.write_text(json.dumps(info))
    _write_mp4(remote / "videos" / "chunk-000" / "obs.wrist" / "episode_000000.mp4")
    provider = BlobDatasetProvider(account_name="testaccount", container_name="testcontainer", sas_token="test")
    transfers = []

    async def sync_meta(dataset_id: str, target: Path) -> bool:
        shutil.copytree(remote / "meta", target / "meta", dirs_exist_ok=True)
        return True

    async def properties(path: str) -> dict[str, object] | None:
        source = tmp_path / "remote" / path
        return {"size": source.stat().st_size, "etag": str(source.stat().st_mtime_ns)} if source.is_file() else None

    async def stream(path: str, **kwargs: object) -> object:
        transfers.append(path)
        yield (tmp_path / "remote" / path).read_bytes()

    async def sdk_properties() -> SimpleNamespace:
        return SimpleNamespace(etag=str(info_path.stat().st_mtime_ns))

    sdk = MagicMock()
    sdk.get_container_client.return_value.get_blob_client.return_value.get_blob_properties = AsyncMock(
        side_effect=sdk_properties
    )
    monkeypatch.setattr(provider, "_get_client", AsyncMock(return_value=sdk))
    monkeypatch.setattr(provider, "get_info_json", AsyncMock(return_value=info))
    monkeypatch.setattr(provider, "sync_meta_only_to_local", sync_meta)
    monkeypatch.setattr(provider, "get_blob_properties", properties)
    monkeypatch.setattr(provider, "stream_video", stream)
    monkeypatch.setattr(
        provider, "_read_blob_bytes", AsyncMock(side_effect=lambda path: (tmp_path / "remote" / path).read_bytes())
    )
    client = _reload_app(restore_default_app, tmp_path / "local")
    datasets = DatasetService(base_path=str(tmp_path / "local"), blob_provider=provider)
    client.app.dependency_overrides[get_dataset_service] = lambda: datasets
    try:
        snapshot = client.get(f"/api/datasets/{DATASET_ID}/episodes/0/judge/snapshot")
        assert snapshot.status_code == 200, snapshot.text
        assert transfers == []
        payload = {"views": ["obs.wrist"], "snapshot_id": snapshot.json()["snapshot_id"]}
        first = client.post(f"/api/datasets/{DATASET_ID}/episodes/0/judge", json=payload)
        assert _finish_job(client, first)["status"] == "succeeded"
        assert len(transfers) == 1 and "/obs.wrist/" in transfers[0]
        transfers.clear()
        second = client.post(f"/api/datasets/{DATASET_ID}/episodes/0/judge", json=payload)
        assert _finish_job(client, second)["targets"][0]["cached"] is True
        assert transfers == []
    finally:
        client.app.dependency_overrides.clear()
        datasets.cleanup_temp_dirs()


def _write_mp4(path: Path, *, n_frames: int = 12, width: int = 64, height: int = 48, fps: int = 30) -> None:
    """Encode a tiny solid-color H.264 clip so frame extraction has real video."""
    path.parent.mkdir(parents=True, exist_ok=True)
    container = av.open(str(path), mode="w")
    stream = container.add_stream("libx264", rate=fps)
    stream.width = width
    stream.height = height
    stream.pix_fmt = "yuv420p"
    for i in range(n_frames):
        arr = np.full((height, width, 3), (i * 17) % 256, dtype=np.uint8)
        frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def _build_dataset(root: Path, *, instruction: str | None, n_frames: int = 12) -> None:
    """Materialize a minimal single-episode LeRobot v2.1 dataset at ``root``."""
    (root / "meta").mkdir(parents=True, exist_ok=True)
    data_path = root / "data" / "chunk-000" / "episode_000000.parquet"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table(
            {
                "frame_index": list(range(n_frames)),
                "episode_index": [0] * n_frames,
                "timestamp": np.arange(n_frames) / 30,
                "observation.state": [[0.0]] * n_frames,
                "action": [[0.0]] * n_frames,
            }
        ),
        data_path,
    )
    _write_mp4(root / "videos" / "chunk-000" / "obs.front" / "episode_000000.mp4", n_frames=n_frames)
    (root / "meta" / "info.json").write_text(
        json.dumps(
            {
                "codebase_version": "v2.1",
                "fps": 30,
                "total_episodes": 1,
                "chunks_size": 1000,
                "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
                "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
                "features": {"obs.front": {"dtype": "video", "shape": [48, 64, 3], "names": ["h", "w", "c"]}},
            },
        ),
    )
    (root / "meta" / "tasks.jsonl").write_text("")
    episode: dict[str, object] = {"episode_index": 0, "length": n_frames}
    episode["tasks"] = [instruction] if instruction else []
    (root / "meta" / "episodes.jsonl").write_text(json.dumps(episode) + "\n")


def _reload_app(environment_patch: pytest.MonkeyPatch, data_dir: Path) -> TestClient:
    """Reload the API with the VLM judge enabled and the echo backend."""
    environment_patch.setenv("DATAVIEWER_AUTH_DISABLED", "true")
    environment_patch.setenv("DATA_DIR", str(data_dir))
    environment_patch.setenv("VLM_JUDGE_ENABLED", "true")
    environment_patch.setenv("VLM_JUDGE_BACKEND", "echo")
    environment_patch.setenv("VLM_JUDGE_N_FRAMES", "6")

    import src.api.config as config_mod
    import src.api.services.annotation_service as ann_service_mod
    import src.api.services.dataset_service.service as ds_service_mod
    from src.api.services.vlm_judge_service import reset_vlm_judge_service

    config_mod._app_config = None
    ann_service_mod._annotation_service = None
    ds_service_mod._dataset_service = None
    reset_vlm_judge_service()

    # main.py snapshots config at import time; force a fresh module import.
    import src.api.main as main_mod

    main_mod = importlib.reload(main_mod)
    return TestClient(main_mod.app)


@pytest.fixture(autouse=True)
def restore_default_app() -> Iterator[pytest.MonkeyPatch]:
    """Restore the default router set after each VLM-enabled app reload."""
    environment_patch = pytest.MonkeyPatch()
    yield environment_patch
    environment_patch.undo()

    import src.api.config as config_mod
    import src.api.services.annotation_service as ann_service_mod
    import src.api.services.dataset_service.service as ds_service_mod
    from src.api.services.vlm_judge_service import reset_vlm_judge_service

    config_mod._app_config = None
    ann_service_mod._annotation_service = None
    ds_service_mod._dataset_service = None
    reset_vlm_judge_service()

    import src.api.main as main_mod

    importlib.reload(main_mod)


def test_restore_default_app_uses_restored_data_dir(tmp_path: Path) -> None:
    default_data_dir = os.environ["DATA_DIR"]
    cleanup = restore_default_app.__wrapped__()
    environment_patch = next(cleanup)
    _reload_app(environment_patch, tmp_path)

    with pytest.raises(StopIteration):
        next(cleanup)

    import src.api.main as main_mod

    assert main_mod._config.data_path == default_data_dir


@pytest.fixture
def vlm_client(tmp_path: Path, restore_default_app: pytest.MonkeyPatch) -> TestClient:
    """Build a TestClient backed by a synthetic dataset under ``tmp_path``."""
    _build_dataset(tmp_path / DATASET_ID, instruction=INSTRUCTION)
    return _reload_app(restore_default_app, tmp_path)


def test_get_returns_uncached_status_initially(vlm_client: TestClient) -> None:
    rsp = vlm_client.get(f"/api/datasets/{DATASET_ID}/episodes/0/judge")
    assert rsp.status_code == 200
    body = rsp.json()
    assert body["enabled"] is True
    assert body["cached"] is False
    assert body["result"] is None
    assert body["judge_model"] == "Qwen/Qwen3-VL-4B-Instruct"
    assert body["prompt_version"].startswith("outcome-mcq-v1")


def test_get_reports_disabled_when_service_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    restore_default_app: pytest.MonkeyPatch,
) -> None:
    _build_dataset(tmp_path / DATASET_ID, instruction=INSTRUCTION)
    client = _reload_app(restore_default_app, tmp_path)

    import src.api.routers.vlm_judge as router_mod

    monkeypatch.setattr(router_mod, "get_vlm_judge_service", lambda _config: None)

    rsp = client.get(f"/api/datasets/{DATASET_ID}/episodes/0/judge")
    assert rsp.status_code == 200
    assert rsp.json() == {
        "enabled": False,
        "cached": False,
        "judge_model": None,
        "prompt_version": None,
        "cache_key": None,
        "backend": None,
        "process_method": None,
        "process_methods": [],
        "n_frames": None,
        "result": None,
    }

    rsp = client.post(f"/api/datasets/{DATASET_ID}/episodes/0/judge", json={})
    assert rsp.status_code == 503
    assert "VLM judge is disabled" in rsp.json()["detail"]


def test_post_runs_judge_and_warms_cache(vlm_client: TestClient) -> None:
    path = f"/api/datasets/{DATASET_ID}/episodes/0/judge"
    rsp = vlm_client.post(
        path,
        json={"force": True},
    )
    assert rsp.status_code == 202
    assert rsp.json()["status"] == "queued"
    body = _finish_job(vlm_client, rsp)["targets"][0]["result"]
    assert body["episode_id"] == f"{DATASET_ID}/episode_000000"
    assert body["instruction"] == INSTRUCTION
    assert body["outcome_success"] is True
    assert body["outcome_n_valid_votes"] == 3
    assert len(body["progress_per_frame"]) == 6
    assert isinstance(body["voc"], float)

    # Second GET should now report cached state with the same payload echoed back.
    rsp2 = vlm_client.get(path)
    assert rsp2.status_code == 200
    status = rsp2.json()
    assert status["cached"] is True
    assert status["result"]["episode_id"] == body["episode_id"]


def _finish_job(client: TestClient, response: object) -> dict[str, object]:
    import asyncio

    assert response.status_code == 202, response.text
    assert asyncio.run(client.app.state.judge_jobs.run_once())
    status = client.get(response.headers["Location"])
    assert status.status_code == 200, status.text
    return status.json()


def test_post_rejects_unsaved_instruction_even_when_forced(vlm_client: TestClient) -> None:
    path = f"/api/datasets/{DATASET_ID}/episodes/0/judge"
    rsp = vlm_client.post(
        path,
        json={"instruction": "Open the drawer", "force": True},
    )
    assert rsp.status_code == 422
    assert "saved" in rsp.json()["detail"].lower()


def _save_instruction(client: TestClient, instruction: str) -> None:
    path = f"/api/datasets/{DATASET_ID}/episodes/0/annotations"
    current = client.get(path)
    headers = {"If-Match": current.headers["ETag"]} if "ETag" in current.headers else {"If-None-Match": "*"}
    response = client.put(
        path,
        headers=headers,
        json={
            "annotator_id": "ignored-client-author",
            "timestamp": "2026-10-08T00:00:00Z",
            "task_completeness": {"rating": "success", "confidence": 3},
            "trajectory_quality": {
                "overall_score": 3,
                "metrics": {"smoothness": 3, "efficiency": 3, "safety": 3, "precision": 3},
                "flags": [],
            },
            "data_quality": {"overall_quality": "good", "issues": []},
            "anomalies": {"anomalies": []},
            "language_instruction": {"instruction": instruction, "source": "human"},
        },
    )
    assert response.status_code == 200, response.text


def test_saved_instruction_snapshot_rejects_later_human_revision(vlm_client: TestClient) -> None:
    _save_instruction(vlm_client, "Saved instruction")
    path = f"/api/datasets/{DATASET_ID}/episodes/0/judge"
    snapshot = vlm_client.get(f"{path}/snapshot")
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["instruction"] == "Saved instruction"
    assert snapshot.json()["instruction_origin"] == "annotation"
    assert snapshot.json()["annotation_revision"]
    _save_instruction(vlm_client, "A newer saved instruction")
    response = vlm_client.post(path, json={"snapshot_id": snapshot.json()["snapshot_id"], "force": True})
    assert response.status_code == 409
    assert "snapshot" in response.json()["detail"].lower()


def test_saved_instruction_is_used_without_dataset_instruction(
    tmp_path: Path,
    restore_default_app: pytest.MonkeyPatch,
) -> None:
    _build_dataset(tmp_path / DATASET_ID, instruction=None)
    client = _reload_app(restore_default_app, tmp_path)
    _save_instruction(client, "Saved instruction only")
    response = client.post(f"/api/datasets/{DATASET_ID}/episodes/0/judge", json={"force": True})
    assert _finish_job(client, response)["targets"][0]["result"]["instruction"] == "Saved instruction only"


def test_snapshot_rejects_source_replacement(vlm_client: TestClient, tmp_path: Path) -> None:
    path = f"/api/datasets/{DATASET_ID}/episodes/0/judge"
    snapshot = vlm_client.get(f"{path}/snapshot").json()
    manifest = tmp_path / DATASET_ID / "meta" / "info.json"
    contents = json.loads(manifest.read_text())
    contents["fps"] = 24
    manifest.write_text(json.dumps(contents))
    response = vlm_client.post(path, json={"snapshot_id": snapshot["snapshot_id"], "force": True})
    assert response.status_code == 409


def test_snapshot_is_principal_scoped(vlm_client: TestClient) -> None:
    from src.api.auth import PrincipalContext, require_principal_context

    path = f"/api/datasets/{DATASET_ID}/episodes/0/judge"
    snapshot = vlm_client.get(f"{path}/snapshot").json()
    vlm_client.app.dependency_overrides[require_principal_context] = lambda: PrincipalContext(
        scope_id="another-principal", auth_mode="local"
    )
    try:
        response = vlm_client.post(path, json={"snapshot_id": snapshot["snapshot_id"], "force": True})
        assert response.status_code == 409
    finally:
        vlm_client.app.dependency_overrides.pop(require_principal_context)


def test_conflicting_authors_require_explicit_selection(vlm_client: TestClient) -> None:
    from src.api.auth import PrincipalContext, require_principal_context

    _save_instruction(vlm_client, "First author's instruction")
    vlm_client.app.dependency_overrides[require_principal_context] = lambda: PrincipalContext(
        scope_id="another-principal", auth_mode="local"
    )
    try:
        _save_instruction(vlm_client, "Second author's instruction")
        path = f"/api/datasets/{DATASET_ID}/episodes/0/judge"
        assert vlm_client.post(path, json={"force": True}).status_code == 409
        selected = vlm_client.get(f"{path}/snapshot", params={"annotation_author_id": "another-principal"})
        assert selected.status_code == 200
        assert selected.json()["instruction"] == "Second author's instruction"
    finally:
        vlm_client.app.dependency_overrides.pop(require_principal_context)


def test_saved_media_edits_block_judging_and_clearing_invalidates_snapshot(vlm_client: TestClient) -> None:
    path = f"/api/datasets/{DATASET_ID}/episodes/0"
    original = vlm_client.get(f"{path}/judge/snapshot").json()
    state = vlm_client.get(f"{path}/edits")
    assert state.status_code == 200, state.text
    body = {
        "source_id": state.json()["source_id"],
        "source_revision": state.json()["source_revision"],
        "operations": {"datasetId": DATASET_ID, "episodeIndex": 0, "removedFrames": [1]},
    }
    saved = vlm_client.put(f"{path}/edits", headers={"If-None-Match": "*"}, json=body)
    assert saved.status_code == 200, saved.text
    assert vlm_client.post(f"{path}/judge", json={"force": True}).status_code == 422
    body["operations"]["removedFrames"] = []
    cleared = vlm_client.put(f"{path}/edits", headers={"If-Match": saved.headers["ETag"]}, json=body)
    assert cleared.status_code == 200, cleared.text
    assert (
        vlm_client.post(f"{path}/judge", json={"snapshot_id": original["snapshot_id"], "force": True}).status_code
        == 409
    )
    fresh = vlm_client.get(f"{path}/judge/snapshot")
    assert fresh.status_code == 200
    assert fresh.json()["edit_revision"] == cleared.headers["ETag"]


def test_source_change_during_inference_does_not_return_success(
    vlm_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evaluation.vlm_judge.service import JudgeService

    original_run = JudgeService.judge_episode

    def replace_after_run(self: JudgeService, **kwargs: object) -> object:
        result = original_run(self, **kwargs)
        manifest = tmp_path / DATASET_ID / "meta" / "info.json"
        manifest.write_text(manifest.read_text() + "\n")
        return result

    monkeypatch.setattr(JudgeService, "judge_episode", replace_after_run)
    response = vlm_client.post(f"/api/datasets/{DATASET_ID}/episodes/0/judge", json={"force": True})
    job = _finish_job(vlm_client, response)
    assert job["status"] == "failed"
    assert job["targets"][0]["error"] == "SavedInputError"
    assert job["targets"][0]["result"] is None


def test_post_falls_back_to_dataset_instruction_when_request_omits_instruction(
    tmp_path: Path,
    restore_default_app: pytest.MonkeyPatch,
) -> None:
    dataset_root = tmp_path / DATASET_ID
    _build_dataset(dataset_root, instruction="Dataset fallback instruction")
    client = _reload_app(restore_default_app, tmp_path)
    path = f"/api/datasets/{DATASET_ID}/episodes/0/judge"

    rsp = client.post(path, json={"force": True})

    assert _finish_job(client, rsp)["targets"][0]["result"]["instruction"] == "Dataset fallback instruction"


def test_post_accepts_safe_view_filter(vlm_client: TestClient) -> None:
    path = f"/api/datasets/{DATASET_ID}/episodes/0/judge"
    rsp = vlm_client.post(
        path,
        json={"views": ["obs.front"], "force": True},
    )
    assert _finish_job(vlm_client, rsp)["targets"][0]["result"]["episode_id"] == f"{DATASET_ID}/episode_000000"


def test_post_rejects_invalid_process_method(vlm_client: TestClient) -> None:
    rsp = vlm_client.post(
        f"/api/datasets/{DATASET_ID}/episodes/0/judge",
        json={"process_method": "reverse", "force": True},
    )
    assert rsp.status_code == 422
    assert "process_method must be one of" in rsp.json()["detail"]


@pytest.mark.parametrize("view", ["../obs.front", "obs/front", "obs\\front", "\x00front"])
def test_post_rejects_unsafe_view_names(vlm_client: TestClient, view: str) -> None:
    rsp = vlm_client.post(
        f"/api/datasets/{DATASET_ID}/episodes/0/judge",
        json={"views": [view], "force": True},
    )
    assert rsp.status_code == 422


def test_post_rejects_dataset_id_that_resolves_to_base_path(vlm_client: TestClient) -> None:
    rsp = vlm_client.post(
        f"/api/datasets/{DATASET_ID}--../episodes/0/judge",
        json={"force": True},
    )
    assert rsp.status_code == 400
    assert "Path traversal" in rsp.json()["detail"]


def test_post_returns_404_for_missing_dataset(vlm_client: TestClient) -> None:
    rsp = vlm_client.post(
        "/api/datasets/does-not-exist/episodes/0/judge",
        json={},
    )
    assert rsp.status_code == 404


def test_post_returns_404_for_missing_episode(vlm_client: TestClient) -> None:
    rsp = vlm_client.post(
        f"/api/datasets/{DATASET_ID}/episodes/7/judge",
        json={"force": True},
    )
    assert rsp.status_code == 404


def test_post_returns_422_when_no_instruction_available(
    tmp_path: Path,
    restore_default_app: pytest.MonkeyPatch,
) -> None:
    # Build a dataset that intentionally omits the task instruction.
    _build_dataset(tmp_path / "no-instruction", instruction=None)
    client = _reload_app(restore_default_app, tmp_path)
    rsp = client.post("/api/datasets/no-instruction/episodes/0/judge", json={})
    assert rsp.status_code == 422
