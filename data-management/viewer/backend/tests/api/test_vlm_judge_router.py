"""Tests for the VLM-as-judge router.

These tests run the full FastAPI app with the echo backend so no GPU or
network is required. They drive the real ``evaluation.vlm_judge`` package
end-to-end (frame extraction, agent, cache).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[5]
DATASETS_DIR = REPO_ROOT / "datasets"
LEISAAC_PRESENT = (DATASETS_DIR / "leisaac-pick-orange" / "meta" / "info.json").exists()


pytestmark = pytest.mark.skipif(
    not LEISAAC_PRESENT,
    reason="leisaac-pick-orange dataset not present in this checkout",
)


@pytest.fixture
def vlm_client(tmp_path: Path) -> TestClient:
    """Build a TestClient with the VLM judge enabled and the echo backend."""
    os.environ["DATAVIEWER_AUTH_DISABLED"] = "true"
    os.environ["DATA_DIR"] = str(DATASETS_DIR)
    os.environ["VLM_JUDGE_ENABLED"] = "true"
    os.environ["VLM_JUDGE_BACKEND"] = "echo"
    os.environ["VLM_JUDGE_N_FRAMES"] = "6"
    os.environ["VLM_JUDGE_CACHE_DIR"] = str(tmp_path / "vlm-cache")

    import src.api.config as config_mod
    import src.api.services.dataset_service as ds_mod
    import src.api.services.vlm_judge_service as vjs_mod

    config_mod._app_config = None
    ds_mod._dataset_service = None
    vjs_mod._service = None

    # main.py snapshots config at import time; force a fresh module import
    import importlib

    import src.api.main as main_mod

    main_mod = importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_get_returns_uncached_status_initially(vlm_client: TestClient) -> None:
    rsp = vlm_client.get("/api/datasets/leisaac-pick-orange/episodes/0/judge")
    assert rsp.status_code == 200
    body = rsp.json()
    assert body["enabled"] is True
    assert body["cached"] is False
    assert body["result"] is None
    assert body["judge_model"] == "Qwen/Qwen3-VL-4B-Instruct"
    assert body["prompt_version"].startswith("outcome-mcq-v1")


def test_post_runs_judge_and_warms_cache(vlm_client: TestClient) -> None:
    rsp = vlm_client.post(
        "/api/datasets/leisaac-pick-orange/episodes/0/judge",
        json={"force": True},
    )
    assert rsp.status_code == 200
    body = rsp.json()
    assert body["episode_id"] == "leisaac-pick-orange/episode_000000"
    assert body["instruction"] == "Grab orange and place into plate"
    assert body["outcome_success"] is True
    assert body["outcome_n_valid_votes"] == 3
    assert len(body["progress_per_frame"]) == 6
    assert isinstance(body["voc"], float)

    # Second GET should now report cached state with the same payload echoed back.
    rsp2 = vlm_client.get("/api/datasets/leisaac-pick-orange/episodes/0/judge")
    assert rsp2.status_code == 200
    status = rsp2.json()
    assert status["cached"] is True
    assert status["result"]["episode_id"] == body["episode_id"]


def test_post_returns_404_for_missing_dataset(vlm_client: TestClient) -> None:
    rsp = vlm_client.post(
        "/api/datasets/does-not-exist/episodes/0/judge",
        json={},
    )
    assert rsp.status_code == 404


def test_post_returns_422_when_no_instruction_available(
    vlm_client: TestClient,
    tmp_path: Path,
) -> None:
    # Build a stripped-down dataset that intentionally omits the task instruction.
    dataset = tmp_path / "no-instruction"
    (dataset / "meta").mkdir(parents=True)
    (dataset / "videos" / "chunk-000" / "obs.front").mkdir(parents=True)
    src = DATASETS_DIR / "leisaac-pick-orange" / "videos" / "chunk-000" / "observation.images.front"
    sample = next(src.glob("episode_000000.mp4"))
    target = dataset / "videos" / "chunk-000" / "obs.front" / "episode_000000.mp4"
    target.write_bytes(sample.read_bytes())
    (dataset / "meta" / "info.json").write_text(
        '{"codebase_version": "v2.1","fps": 30,"total_episodes": 1,"chunks_size": 1000,'
        '"data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",'
        '"video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",'
        '"features": {"obs.front": {"dtype": "video", "shape": [480,640,3], "names": ["h","w","c"]}}}',
    )
    (dataset / "meta" / "tasks.jsonl").write_text("")
    (dataset / "meta" / "episodes.jsonl").write_text(
        '{"episode_index": 0, "tasks": [], "length": 30}\n',
    )

    # Point the dataset service at the temp dir for this test only.
    os.environ["DATA_DIR"] = str(tmp_path)
    import importlib

    import src.api.config as config_mod
    import src.api.main as main_mod
    import src.api.services.dataset_service as ds_mod

    config_mod._app_config = None
    ds_mod._dataset_service = None
    main_mod = importlib.reload(main_mod)
    client = TestClient(main_mod.app)

    rsp = client.post("/api/datasets/no-instruction/episodes/0/judge", json={})
    assert rsp.status_code == 422
