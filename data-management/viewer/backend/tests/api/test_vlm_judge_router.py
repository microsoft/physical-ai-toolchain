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
import shutil
import subprocess
import sys
import sysconfig
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import av
import numpy as np
import pytest
from fastapi.testclient import TestClient

DATASET_ID = "synthetic-eval"
INSTRUCTION = "Pick up the cube"


@pytest.mark.parametrize("enabled", ["false", "true"])
def test_backend_starts_without_optional_evaluator(tmp_path: Path, enabled: str) -> None:
    app_dir = tmp_path / "app"
    shutil.copytree(Path(__file__).resolve().parents[2] / "src", app_dir / "src")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    environment = {
        **os.environ,
        "STORAGE_BACKEND": "local",
        "DATA_DIR": str(data_dir),
        "DATAVIEWER_AUTH_DISABLED": "true",
        "VLM_JUDGE_ENABLED": enabled,
        "VLM_JUDGE_BACKEND": "echo",
    }
    script = """
import importlib.util
import sys

sys.path.insert(0, sys.argv[1])
sys.path.append(sys.argv[2])
assert importlib.util.find_spec("evaluation") is None

from fastapi.testclient import TestClient
from src.api.main import app
from src.api.config import get_app_config
from src.api.services.vlm_judge_service import get_vlm_judge_service

with TestClient(app) as client:
    assert client.get("/health").status_code == 200
    assert get_vlm_judge_service(get_app_config()) is None
"""

    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, str(app_dir), sysconfig.get_path("purelib")],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


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


def test_post_instruction_override_is_used(vlm_client: TestClient) -> None:
    path = f"/api/datasets/{DATASET_ID}/episodes/0/judge"
    rsp = vlm_client.post(
        path,
        json={"instruction": "Open the drawer", "force": True},
    )
    assert rsp.status_code == 200
    assert rsp.json()["instruction"] == "Open the drawer"


def test_backend_and_pin_changes_invalidate_service_and_viewer_cache(
    vlm_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evaluation.vlm_judge.backend import EchoBackend
    from evaluation.vlm_judge.service import JudgeService

    import src.api.routers.vlm_judge as router_mod
    from src.api.config import get_app_config
    from src.api.services.vlm_judge_service import get_vlm_judge_service

    original = get_vlm_judge_service(get_app_config())
    assert original is not None
    base = original.config
    calls = []

    def fake_backend(config: object) -> EchoBackend:
        calls.append(config)
        return EchoBackend()

    monkeypatch.setattr(JudgeService, "_build_backend", staticmethod(fake_backend))
    path = f"/api/datasets/{DATASET_ID}/episodes/0/judge"
    keys = []
    for kind, revision, expected_cached in [
        ("echo", "a" * 40, False),
        ("qwen3-vl", "a" * 40, False),
        ("qwen3-vl", "A" * 40, True),
        ("qwen3-vl", "b" * 40, False),
    ]:
        service = JudgeService(replace(base, backend=replace(base.backend, kind=kind, revision=revision)))
        monkeypatch.setattr(router_mod, "get_vlm_judge_service", lambda _config, selected=service: selected)
        before = vlm_client.get(path).json()
        assert before["cached"] is expected_cached
        response = vlm_client.post(path, json={})
        assert response.status_code == 200
        assert response.json()["cached"] is expected_cached
        after = vlm_client.get(path).json()
        assert after["cached"] is True
        keys.append(after["cache_key"])

    assert len(calls) == 3
    assert keys[1] == keys[2]
    assert len(set(keys)) == 3


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


def _set_video_template(root: Path, version: str, template: str) -> None:
    info_path = root / "meta" / "info.json"
    info = json.loads(info_path.read_text())
    info["codebase_version"] = version
    info["video_path"] = template
    info["features"]["obs.front"]["videos_path"] = template
    info_path.write_text(json.dumps(info))


@pytest.mark.parametrize("version", ["v2.1", "v3.0"])
@pytest.mark.parametrize("case", ["outside", "missing-outside", "symlink"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_http_media_rejected_before_probe_or_cache(
    vlm_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: str,
    case: str,
    method: str,
) -> None:
    from evaluation.vlm_judge.cache import JudgeCache
    from evaluation.vlm_judge.service import JudgeService

    root = tmp_path / DATASET_ID
    target = tmp_path / "outside.mp4"
    if case != "missing-outside":
        _write_mp4(target)
    path = f"/api/datasets/{DATASET_ID}/episodes/0/judge"
    assert vlm_client.post(path, json={}).status_code == 200
    if case == "symlink":
        alias = root / "escape.mp4"
        alias.symlink_to(target)
        template = str(alias)
    else:
        template = str(target)
    _set_video_template(root, version, template)
    original_exists = Path.exists

    def guarded_exists(candidate: Path) -> bool:
        if candidate == target or candidate.name == "escape.mp4":
            pytest.fail("HTTP media reached the adapter existence probe")
        return original_exists(candidate)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Rejected media reached cache, decoder or model")

    monkeypatch.setattr(Path, "exists", guarded_exists)
    monkeypatch.setattr(JudgeCache, "key", forbidden)
    monkeypatch.setattr(JudgeService, "_extract", forbidden)
    monkeypatch.setattr(JudgeService, "_build_backend", forbidden)

    response = vlm_client.request(method, path, **({"json": {}} if method == "POST" else {}))

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid media input"}


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_selected_v3_episode_ignores_missing_unselected_media(
    vlm_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    root = tmp_path / DATASET_ID
    _set_video_template(root, "v3.0", "videos/chunk-000/obs.front/episode_{episode_index:06d}.mp4")
    missing = root / "videos/chunk-000/obs.front/episode_000000.mp4"
    missing.rename(missing.with_name("episode_000001.mp4"))
    info_path = root / "meta/info.json"
    info = json.loads(info_path.read_text())
    info["total_episodes"] = 2
    info_path.write_text(json.dumps(info))
    (root / "meta/episodes.jsonl").write_text(
        "".join(json.dumps({"episode_index": index, "length": 12, "tasks": [INSTRUCTION]}) + "\n" for index in (0, 1))
    )
    original_stat = Path.stat

    def guarded_stat(candidate: Path, *args: object, **kwargs: object) -> os.stat_result:
        if candidate == missing:
            pytest.fail("Unselected episode reached a media probe")
        return original_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", guarded_stat)
    path = f"/api/datasets/{DATASET_ID}/episodes/1/judge"
    response = vlm_client.request(method, path, **({"json": {}} if method == "POST" else {}))

    assert response.status_code == 200
    first = vlm_client.post(path, json={})
    assert first.status_code == 200
    assert first.json()["cached"] is (method == "POST")
    assert vlm_client.post(path, json={}).json()["cached"] is True
    assert vlm_client.get(path).json()["cached"] is True


@pytest.mark.parametrize("indices", [None, [], [0], [1]])
@pytest.mark.parametrize("validate_media", [False, True])
def test_v3_jsonl_selection_preserves_trusted_and_validated_iteration(
    tmp_path: Path, indices: list[int] | None, validate_media: bool
) -> None:
    from evaluation.vlm_judge.dataset import iter_episodes
    from evaluation.vlm_judge.frames import validate_video_path

    root = tmp_path / DATASET_ID
    _build_dataset(root, instruction=INSTRUCTION)
    _set_video_template(root, "v3.0", "videos/chunk-000/obs.front/episode_{episode_index:06d}.mp4")
    _write_mp4(root / "videos/chunk-000/obs.front/episode_000001.mp4")
    (root / "meta/episodes.jsonl").write_text(
        "".join(json.dumps({"episode_index": index, "length": 12, "tasks": [INSTRUCTION]}) + "\n" for index in (0, 1))
    )
    validated = []

    def validator(candidate: Path) -> Path:
        validated.append(candidate)
        return validate_video_path(candidate, root)

    records = list(iter_episodes(root, indices=indices, video_validator=validator if validate_media else None))

    assert [record.episode_index for record in records] == ([0, 1] if indices is None else indices)
    expected_paths = {path for record in records for path in record.video_paths.values()}
    assert set(validated) == (expected_paths if validate_media else set())


def test_contained_v3_jsonl_episode_judges_and_reuses_cache(vlm_client: TestClient, tmp_path: Path) -> None:
    _set_video_template(tmp_path / DATASET_ID, "v3.0", "videos/chunk-000/obs.front/episode_{episode_index:06d}.mp4")
    path = f"/api/datasets/{DATASET_ID}/episodes/0/judge"

    first = vlm_client.post(path, json={})
    second = vlm_client.post(path, json={})

    assert first.status_code == second.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert vlm_client.get(path).json()["cached"] is True


@pytest.mark.parametrize("failure", ["corrupt", "window", "backend"])
def test_media_and_backend_failures_do_not_disclose_paths(
    vlm_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    import evaluation.vlm_judge.dataset as dataset_module
    from evaluation.vlm_judge.service import JudgeService

    video = tmp_path / DATASET_ID / "videos/chunk-000/obs.front/episode_000000.mp4"
    if failure == "corrupt":
        video.write_bytes(b"corrupt media")
    elif failure == "window":
        original = dataset_module.iter_episodes

        def invalid_window(*args: object, **kwargs: object) -> Iterator[dataset_module.EpisodeRecord]:
            for record in original(*args, **kwargs):
                yield replace(record, from_timestamp=2.0, to_timestamp=1.0)

        monkeypatch.setattr(dataset_module, "iter_episodes", invalid_window)

    def no_inference(*args: object, **kwargs: object) -> None:
        if failure == "backend":
            raise ValueError(f"private backend details {video}")
        pytest.fail("Invalid decoded media reached inference")

    monkeypatch.setattr(JudgeService, "_build_backend", no_inference)
    response = vlm_client.post(f"/api/datasets/{DATASET_ID}/episodes/0/judge", json={})

    assert response.status_code == (502 if failure == "backend" else 400)
    assert response.json() == {"detail": "VLM backend error" if failure == "backend" else "Invalid media input"}
