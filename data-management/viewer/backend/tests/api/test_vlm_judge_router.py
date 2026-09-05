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
from pathlib import Path

import av
import h5py
import numpy as np
import pytest
from fastapi.testclient import TestClient

DATASET_ID = "synthetic-eval"
INSTRUCTION = "Pick up the cube"


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


def _build_hdf5_dataset(root: Path, *, instruction: str, n_frames: int = 12) -> None:
    """Materialize one HDF5 episode with embedded camera frames and task metadata."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "meta").mkdir()
    (root / "meta" / "dataset.json").write_text(json.dumps({"task": instruction}))
    frames = np.stack(
        [np.full((48, 64, 3), (index * 17) % 256, dtype=np.uint8) for index in range(n_frames)],
    )
    with h5py.File(root / "episode_000000.hdf5", "w") as episode:
        episode.create_dataset("data/qpos", data=np.zeros((n_frames, 6), dtype=np.float32))
        episode.create_dataset("data/qvel", data=np.zeros((n_frames, 6), dtype=np.float32))
        episode.create_dataset("data/ee_pose", data=np.zeros((n_frames, 6), dtype=np.float32))
        episode.create_dataset("data/gripper", data=np.zeros(n_frames, dtype=np.float32))
        episode.create_dataset("data/action", data=np.zeros((n_frames, 6), dtype=np.float32))
        episode.create_dataset("data/timestamps", data=np.arange(n_frames) / 30)
        episode.create_dataset("observations/images/wrist", data=frames)
        episode.attrs["fps"] = 30.0
        episode.attrs["task_index"] = 0


def _reload_app(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> TestClient:
    """Reload the API with the VLM judge enabled and the echo backend."""
    monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "true")
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("VLM_JUDGE_ENABLED", "true")
    monkeypatch.setenv("VLM_JUDGE_BACKEND", "echo")
    monkeypatch.setenv("VLM_JUDGE_N_FRAMES", "6")

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


@pytest.fixture
def vlm_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Build a TestClient backed by a synthetic dataset under ``tmp_path``."""
    _build_dataset(tmp_path / DATASET_ID, instruction=INSTRUCTION)
    return _reload_app(monkeypatch, tmp_path)


def test_get_returns_uncached_status_initially(vlm_client: TestClient) -> None:
    rsp = vlm_client.get(f"/api/datasets/{DATASET_ID}/episodes/0/judge")
    assert rsp.status_code == 200
    body = rsp.json()
    assert body["enabled"] is True
    assert body["cached"] is False
    assert body["result"] is None
    assert body["judge_model"] == "Qwen/Qwen3-VL-4B-Instruct"
    assert body["prompt_version"].startswith("outcome-mcq-v1")


def test_get_reports_disabled_when_service_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _build_dataset(tmp_path / DATASET_ID, instruction=INSTRUCTION)
    client = _reload_app(monkeypatch, tmp_path)

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


def test_post_runs_judge_for_hdf5_episode_and_warms_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_id = "hdf5-eval"
    instruction = "Move the gear into the bin"
    dataset_root = tmp_path / dataset_id
    _build_hdf5_dataset(dataset_root, instruction=instruction)
    client = _reload_app(monkeypatch, tmp_path)
    path = f"/api/datasets/{dataset_id}/episodes/0/judge"

    response = client.post(path, json={"force": True})

    assert response.status_code == 200
    result = response.json()
    assert result["episode_id"] == f"{dataset_id}/episode_000000"
    assert result["instruction"] == instruction
    assert result["outcome_success"] is True
    assert len(result["progress_per_frame"]) == 6
    assert (dataset_root / "meta" / "videos" / "wrist" / "episode_000000.mp4").is_file()

    cached = client.get(path)
    assert cached.status_code == 200
    assert cached.json()["cached"] is True
    assert cached.json()["result"]["episode_id"] == result["episode_id"]


def test_post_instruction_override_is_used(vlm_client: TestClient) -> None:
    path = f"/api/datasets/{DATASET_ID}/episodes/0/judge"
    rsp = vlm_client.post(
        path,
        json={"instruction": "Open the drawer", "force": True},
    )
    assert rsp.status_code == 200
    assert rsp.json()["instruction"] == "Open the drawer"


def test_post_falls_back_to_dataset_instruction_when_request_omits_instruction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_root = tmp_path / DATASET_ID
    _build_dataset(dataset_root, instruction="Dataset fallback instruction")
    client = _reload_app(monkeypatch, tmp_path)
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Build a dataset that intentionally omits the task instruction.
    _build_dataset(tmp_path / "no-instruction", instruction=None)
    client = _reload_app(monkeypatch, tmp_path)
    rsp = client.post("/api/datasets/no-instruction/episodes/0/judge", json={})
    assert rsp.status_code == 422
