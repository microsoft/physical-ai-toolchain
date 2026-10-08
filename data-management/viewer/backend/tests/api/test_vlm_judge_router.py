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
        assert first.status_code == 200, first.text
        assert len(transfers) == 1 and "/obs.wrist/" in transfers[0]
        transfers.clear()
        second = client.post(f"/api/datasets/{DATASET_ID}/episodes/0/judge", json=payload)
        assert second.status_code == 200 and second.json()["cached"] is True
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
    assert rsp.status_code == 200
    body = rsp.json()
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
    assert response.status_code == 200, response.text
    assert response.json()["instruction"] == "Saved instruction only"


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
    from src.api.routers import vlm_judge as router

    original_run = router.run_in_threadpool

    async def replace_after_run(*args: object, **kwargs: object) -> object:
        result = await original_run(*args, **kwargs)
        manifest = tmp_path / DATASET_ID / "meta" / "info.json"
        manifest.write_text(manifest.read_text() + "\n")
        return result

    monkeypatch.setattr(router, "run_in_threadpool", replace_after_run)
    response = vlm_client.post(f"/api/datasets/{DATASET_ID}/episodes/0/judge", json={"force": True})
    assert response.status_code == 409


def test_post_falls_back_to_dataset_instruction_when_request_omits_instruction(
    tmp_path: Path,
    restore_default_app: pytest.MonkeyPatch,
) -> None:
    dataset_root = tmp_path / DATASET_ID
    _build_dataset(dataset_root, instruction="Dataset fallback instruction")
    client = _reload_app(restore_default_app, tmp_path)
    path = f"/api/datasets/{DATASET_ID}/episodes/0/judge"

    rsp = client.post(path, json={"force": True})

    assert rsp.status_code == 200
    assert rsp.json()["instruction"] == "Dataset fallback instruction"


def test_post_accepts_safe_view_filter(vlm_client: TestClient) -> None:
    path = f"/api/datasets/{DATASET_ID}/episodes/0/judge"
    rsp = vlm_client.post(
        path,
        json={"views": ["obs.front"], "force": True},
    )
    assert rsp.status_code == 200
    assert rsp.json()["episode_id"] == f"{DATASET_ID}/episode_000000"


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
