"""Unit tests for the dataviewer VLM-judge service factory.

These exercise ``get_vlm_judge_service`` without touching a dataset, the
network, or model weights, so they always run in CI regardless of which
datasets are present.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace

import pytest

import src.api.services.vlm_judge_service as vjs
from src.api.config import AppConfig, load_config


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure each test starts and ends with a clean service singleton."""
    vjs.reset_vlm_judge_service()
    yield
    vjs.reset_vlm_judge_service()


def _config(monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> AppConfig:
    monkeypatch.setenv("VLM_JUDGE_ENABLED", "true" if enabled else "false")
    monkeypatch.setenv("VLM_JUDGE_BACKEND", "echo")
    return load_config()


def _config_with_process_method(monkeypatch: pytest.MonkeyPatch, process_method: str) -> AppConfig:
    monkeypatch.setenv("VLM_JUDGE_ENABLED", "true")
    monkeypatch.setenv("VLM_JUDGE_BACKEND", "echo")
    monkeypatch.setenv("VLM_JUDGE_PROCESS_METHOD", process_method)
    return load_config()


def test_returns_none_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    assert vjs.get_vlm_judge_service(_config(monkeypatch, enabled=False)) is None


@pytest.mark.parametrize("override", ["", "Declared experiment instruction"])
def test_cli_persists_declared_instruction_before_service_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: str
) -> None:
    from evaluation.vlm_judge import run as judge_run
    from evaluation.vlm_judge.dataset import EpisodeRecord
    from evaluation.vlm_judge.service import JudgeService

    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "info.json").write_text("{}")
    episode = EpisodeRecord("synthetic/episode_000000", 0, "Saved dataset instruction", 30, 30, {}, None, None)
    monkeypatch.setattr(judge_run, "iter_episodes", lambda *args, **kwargs: iter([episode]))
    output = tmp_path / "results.jsonl"
    original_build = judge_run._build_service

    def build_with_declared_configuration(args: argparse.Namespace) -> JudgeService:
        declared = json.loads(output.with_suffix(".jsonl.config.json").read_text())
        assert declared["instruction_override"] == (override or None)
        assert declared["instruction_origin"] == ("cli-override" if override else "saved-input")
        assert declared["backend"] == "echo"
        assert "api_key" not in declared
        assert "base_url" not in declared
        return original_build(args)

    monkeypatch.setattr(judge_run, "_build_service", build_with_declared_configuration)
    assert (
        judge_run.main(
            [
                "--dataset",
                str(tmp_path),
                "--output",
                str(output),
                "--backend",
                "echo",
                "--dry-run",
                "--instruction",
                override,
                "--api-key",
                "synthetic-not-a-credential",
            ]
        )
        == 0
    )
    assert json.loads(output.read_text())["instruction"] == (override or episode.instruction)


def test_builds_and_memoizes_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(monkeypatch, enabled=True)
    service = vjs.get_vlm_judge_service(config)
    assert service is not None
    assert service.model_id == "Qwen/Qwen3-VL-4B-Instruct"
    # Second call returns the exact same instance (lazy singleton).
    assert vjs.get_vlm_judge_service(config) is service


def test_reset_drops_the_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(monkeypatch, enabled=True)
    first = vjs.get_vlm_judge_service(config)
    vjs.reset_vlm_judge_service()
    second = vjs.get_vlm_judge_service(config)
    assert first is not None
    assert second is not None
    assert first is not second


def test_returns_none_when_evaluation_package_unimportable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force ``from evaluation.vlm_judge import ...`` to raise ImportError.
    monkeypatch.setitem(sys.modules, "evaluation.vlm_judge", None)
    assert vjs.get_vlm_judge_service(_config(monkeypatch, enabled=True)) is None


def test_warns_when_process_method_env_value_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = _config_with_process_method(monkeypatch, "backwards")

    service = vjs.get_vlm_judge_service(config)

    assert service is not None
    assert service.config.agent.process_method == "gvl"
    assert "Invalid VLM_JUDGE_PROCESS_METHOD='backwards'; falling back to 'gvl'" in caplog.text


def test_builds_singleton_once_for_concurrent_first_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import evaluation.vlm_judge as eval_vlm

    original = eval_vlm.JudgeService
    calls = 0
    guard = Lock()

    class SlowJudgeService(original):
        def __init__(self, *args, **kwargs) -> None:
            nonlocal calls
            with guard:
                calls += 1
            time.sleep(0.01)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(eval_vlm, "JudgeService", SlowJudgeService)
    config = _config(monkeypatch, enabled=True)

    with ThreadPoolExecutor(max_workers=8) as executor:
        services = list(executor.map(lambda _idx: vjs.get_vlm_judge_service(config), range(8)))

    assert calls == 1
    assert all(service is services[0] for service in services)


def _judge_payload() -> dict[str, object]:
    return {
        "episode_id": "synthetic/episode_000000",
        "instruction": "Saved instruction",
        "judge_model": "synthetic-model",
        "prompt_version": "test",
        "n_frames": 2,
        "outcome_success": False,
        "outcome_confidence": 0.8,
        "outcome_n_valid_votes": 3,
        "progress_per_frame": [0, 100],
        "voc": 0.5,
        "milestones": [],
        "failure_mode": None,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcome_success", "false"),
        ("outcome_confidence", float("nan")),
        ("outcome_confidence", 1.1),
        ("outcome_n_valid_votes", True),
        ("n_frames", 0),
        ("progress_per_frame", [0, 101]),
        ("progress_per_frame", [False, 100]),
        ("voc", -1.1),
        ("instruction", ""),
        ("milestones", [{}]),
    ],
)
def test_given_invalid_result_when_decoded_then_rejected(field: str, value: object) -> None:
    from evaluation.vlm_judge.service import _result_from_dict

    payload = {**_judge_payload(), field: value}

    with pytest.raises(ValueError, match="result|Result|milestone"):
        _result_from_dict(payload)


def test_given_missing_result_when_decoded_then_validation_error() -> None:
    from evaluation.vlm_judge.service import _result_from_dict

    with pytest.raises(ValueError, match="result|Result"):
        _result_from_dict({})


@pytest.mark.parametrize(
    "changed",
    ["revision", "kind", "base_url", "device_map", "dtype", "n_frames", "target_size"],
)
def test_given_changed_execution_config_when_judged_then_cache_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    from evaluation.vlm_judge.judge import JudgeResult
    from evaluation.vlm_judge.service import BackendConfig, FrameConfig, JudgeService, ServiceConfig

    original = ServiceConfig(backend=BackendConfig(kind="echo"), frames=FrameConfig(n_frames=2), cache_dir=tmp_path)
    if changed in {"n_frames", "target_size"}:
        frames = replace(original.frames, **{changed: 4 if changed == "n_frames" else (224, 224)})
        modified = replace(original, frames=frames)
    else:
        values = {
            "revision": "new-revision",
            "kind": "openai-compat",
            "base_url": "https://example.invalid/v1",
            "device_map": "cpu",
            "dtype": "float32",
        }
        modified = replace(original, backend=replace(original.backend, **{changed: values[changed]}))
    calls = []

    def judge(**kwargs: object) -> JudgeResult:
        calls.append(kwargs)
        return JudgeResult(**_judge_payload())

    monkeypatch.setattr(JudgeService, "_extract", lambda *args, **kwargs: [])
    monkeypatch.setattr(JudgeService, "_ensure_agent", lambda self: SimpleNamespace(judge=judge))
    request = {
        "episode_id": "synthetic/episode_000000",
        "instruction": "Saved instruction",
        "video_paths": {"front": tmp_path / "front.mp4"},
    }

    JudgeService(original).judge_episode(**request)
    JudgeService(original).judge_episode(**request)
    JudgeService(modified).judge_episode(**request)

    assert len(calls) == 2


def test_given_concurrent_cache_writers_when_published_then_complete_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evaluation.vlm_judge.cache import JudgeCache

    cache = JudgeCache(tmp_path)
    barrier = Barrier(2)
    original_replace = Path.replace

    def publish(path: Path, target: Path) -> Path:
        barrier.wait(timeout=5)
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", publish)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(cache.put, "same-key", _judge_payload()) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)

    assert cache.get("same-key") == _judge_payload()
    assert list(tmp_path.iterdir()) == [tmp_path / "same-key.json"]


def test_given_concurrent_first_inference_when_initialized_then_one_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evaluation.vlm_judge.backend import EchoBackend
    from evaluation.vlm_judge.service import BackendConfig, JudgeService, ServiceConfig

    calls = []
    service = JudgeService(ServiceConfig(backend=BackendConfig(kind="echo")))

    def build(config: BackendConfig) -> EchoBackend:
        calls.append(config)
        time.sleep(0.01)
        return EchoBackend()

    monkeypatch.setattr(service, "_build_backend", build)
    with ThreadPoolExecutor(max_workers=8) as executor:
        agents = list(executor.map(lambda _: service._ensure_agent(), range(8)))

    assert len(calls) == 1
    assert all(agent is agents[0] for agent in agents)


@pytest.mark.parametrize("renderer", ["outcome", "process", "chronological_process", "milestone", "failure"])
def test_given_saved_instruction_when_prompt_rendered_then_preserved(renderer: str) -> None:
    from evaluation.vlm_judge import prompts

    instruction = "Move the {blue} object to its marked destination."
    arguments = {"instruction": instruction}
    if renderer != "failure":
        arguments["n_frames"] = 4
    if renderer == "milestone":
        arguments["outcome_success"] = None

    assert instruction in getattr(prompts, f"render_{renderer}_prompt")(**arguments)


def test_given_partial_cli_execution_when_finished_then_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evaluation.vlm_judge import run as judge_run
    from evaluation.vlm_judge.dataset import EpisodeRecord

    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "info.json").write_text("{}")
    episodes = [
        EpisodeRecord(f"synthetic/{index}", index, "Saved instruction", 30, 30, {}, None, None) for index in (3, 1005)
    ]
    monkeypatch.setattr(judge_run, "iter_episodes", lambda *args, **kwargs: iter(episodes))

    def process(*, episode: EpisodeRecord, **kwargs: object) -> dict[str, object]:
        if episode.episode_index == 1005:
            raise ValueError("Synthetic execution error")
        return _judge_payload()

    monkeypatch.setattr(judge_run, "_process_episode", process)

    assert (
        judge_run.main(["--dataset", str(tmp_path), "--output", str(tmp_path / "results.jsonl"), "--backend", "echo"])
        != 0
    )


@pytest.mark.asyncio
async def test_given_saved_local_instruction_when_resolved_then_author_and_revision_bound(tmp_path: Path) -> None:
    from evaluation.vlm_judge.saved_input import LocalSavedInputReader, resolve_saved_input

    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "info.json").write_text("{}")
    path = tmp_path / "annotations" / "episodes" / "episode_000003.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "annotations": [
                    {"annotator_id": "human", "language_instruction": {"instruction": "Saved human instruction"}}
                ]
            }
        )
    )
    reader = LocalSavedInputReader(tmp_path)
    source = await reader.source_revision("dataset", 3)
    request = {
        "dataset_id": "dataset",
        "episode_index": 3,
        "principal_scope_id": "human",
        "source": source,
        "dataset_instruction": "Original instruction",
    }

    snapshot = await resolve_saved_input(reader, **request)

    assert snapshot.instruction == "Saved human instruction"
    assert snapshot.annotation_author_id == "human"
    assert snapshot.annotation_revision
    path.write_text(
        json.dumps(
            {"annotations": [{"annotator_id": "human", "language_instruction": {"instruction": "Changed instruction"}}]}
        )
    )
    with pytest.raises(ValueError, match="stale"):
        await resolve_saved_input(reader, expected_snapshot_id=snapshot.snapshot_id, **request)


@pytest.mark.asyncio
async def test_given_saved_media_edit_when_cli_resolves_then_unsupported_transform_rejected(tmp_path: Path) -> None:
    import hashlib

    from evaluation.vlm_judge.saved_input import LocalSavedInputReader, resolve_saved_input

    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "info.json").write_text("{}")
    reader = LocalSavedInputReader(tmp_path)
    source = await reader.source_revision("dataset", 3)
    namespace = hashlib.sha256(json.dumps([source[0], "human"], separators=(",", ":")).encode()).hexdigest()
    path = tmp_path / "annotations" / "edits" / namespace / "episode_000003.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "saved_edits": {
                    source[0]: {
                        "human": {
                            "dataset_id": "dataset",
                            "episode_index": 3,
                            "author_id": "human",
                            "source_id": source[0],
                            "source_revision": source[1],
                            "operations": {"removedFrames": [2]},
                        }
                    }
                }
            }
        )
    )

    with pytest.raises(ValueError, match="cannot be rendered"):
        await resolve_saved_input(
            reader, "dataset", 3, principal_scope_id="human", source=source, dataset_instruction="Saved instruction"
        )


def test_given_saved_annotation_when_cli_runs_then_uses_saved_instruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evaluation.vlm_judge import run as judge_run
    from evaluation.vlm_judge.dataset import EpisodeRecord

    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "info.json").write_text("{}")
    path = tmp_path / "annotations" / "episodes" / "episode_000003.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "annotations": [
                    {"annotator_id": "human", "language_instruction": {"instruction": "Saved human instruction"}}
                ]
            }
        )
    )
    episode = EpisodeRecord("dataset/3", 3, "Original instruction", 30, 30, {}, None, None)
    monkeypatch.setattr(judge_run, "iter_episodes", lambda *args, **kwargs: iter([episode]))
    output = tmp_path / "results.jsonl"

    assert judge_run.main(["--dataset", str(tmp_path), "--output", str(output), "--backend", "echo", "--dry-run"]) == 0
    assert json.loads(output.read_text())["instruction"] == "Saved human instruction"
    declared = json.loads(output.with_suffix(".jsonl.config.json").read_text())
    assert declared["saved_inputs"][0]["annotation_author_id"] == "human"


@pytest.mark.parametrize("response", ["{}", "[0, 101]", "[0, true]", "[0, 50.5]", "[0]", "progress: 0 100"])
def test_given_malformed_process_output_when_parsed_then_rejected(response: str) -> None:
    from evaluation.vlm_judge.prompts import parse_process_response

    assert parse_process_response(response, n_frames=2) is None


def test_given_malformed_outcome_when_scored_then_execution_error_without_raw_output(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from evaluation.vlm_judge.judge import score_episode

    backend = SimpleNamespace(name="fake", generate=lambda **kwargs: "{} private-model-output")
    with pytest.raises(ValueError, match="outcome"):
        score_episode(
            backend=backend, episode_id="synthetic/3", instruction="Saved instruction", frames=[object(), object()]
        )
    assert "private-model-output" not in caplog.text


def test_given_unconfigured_standalone_api_when_judged_then_fails_closed() -> None:
    from evaluation.vlm_judge.api import build_router
    from evaluation.vlm_judge.service import BackendConfig, JudgeService, ServiceConfig
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(build_router(JudgeService(ServiceConfig(backend=BackendConfig(kind="echo")))))
    with TestClient(app) as client:
        response = client.post("/judge", json={"dataset_id": "synthetic", "episode_index": 3})
    assert response.status_code == 503
