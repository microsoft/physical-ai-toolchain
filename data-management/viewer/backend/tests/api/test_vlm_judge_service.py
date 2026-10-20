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
from unittest.mock import AsyncMock

import pytest

import src.api.services.vlm_judge_service as vjs
from src.api.config import AppConfig, load_config
from src.api.services.saved_input_service import SavedInputError, ViewerDatasetResolver


@pytest.mark.parametrize(
    "host", ["example.openai.azure.com", "example.services.ai.azure.com", "example.cognitiveservices.azure.com"]
)
@pytest.mark.parametrize("refresh_fails", [False, True])
def test_given_foundry_when_token_expires_then_cli_refreshes_without_static_key_fallback(
    monkeypatch: pytest.MonkeyPatch, host: str, refresh_fails: bool
) -> None:
    import httpx
    import openai
    from azure.core.credentials import AccessTokenInfo
    from azure.core.exceptions import ClientAuthenticationError
    from azure.identity import AzureCliCredential
    from evaluation.vlm_judge.backend import OpenAICompatibleBackend

    clock = [1000.0]
    scopes_seen = []
    headers = []

    def token_info(_credential: AzureCliCredential, *scopes: str, **_kwargs: object) -> AccessTokenInfo:
        scopes_seen.append(scopes)
        if refresh_fails and len(scopes_seen) > 1:
            raise ClientAuthenticationError("Synthetic credential expiry")
        return AccessTokenInfo(f"synthetic-token-{len(scopes_seen)}", int(clock[0]) + 3600)

    def send(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers["Authorization"])
        return httpx.Response(200, json={"object": "list", "data": []}, request=request)

    constructor = openai.OpenAI
    transport = httpx.Client(transport=httpx.MockTransport(send))
    monkeypatch.setattr(openai, "OpenAI", lambda **options: constructor(http_client=transport, **options))
    monkeypatch.setattr(time, "time", lambda: clock[0])
    monkeypatch.setattr(AzureCliCredential, "get_token_info", token_info)
    monkeypatch.setenv("OPENAI_API_KEY", "unused-environment-key")
    backend = OpenAICompatibleBackend(
        model="example", base_url=f"https://{host}/openai/v1/", api_key="unused-explicit-key"
    )
    try:
        backend._client.models.list()
        backend._client.models.list()
        assert headers == ["Bearer synthetic-token-1", "Bearer synthetic-token-1"]
        assert len(scopes_seen) == 1
        clock[0] += 3601
        if refresh_fails:
            with pytest.raises(ClientAuthenticationError, match="Synthetic credential expiry"):
                backend._client.models.list()
            assert len(headers) == 2
        else:
            backend._client.models.list()
            assert headers[-1] == "Bearer synthetic-token-2"
        assert scopes_seen == [("https://cognitiveservices.azure.com/.default",)] * 2
    finally:
        backend._client.close()


@pytest.mark.parametrize("base_url", ["http://localhost:9000/v1", "https://example.openai.azure.com.invalid/v1"])
def test_given_non_azure_endpoint_when_constructing_then_preserves_explicit_key(base_url: str) -> None:
    from evaluation.vlm_judge.backend import OpenAICompatibleBackend

    backend = OpenAICompatibleBackend(model="example", base_url=base_url, api_key="synthetic-key")
    try:
        assert backend._client.api_key == "synthetic-key"
    finally:
        backend._client.close()


@pytest.mark.parametrize(
    "base_url", ["http://example.openai.azure.com/v1", "https://synthetic:credential@example.openai.azure.com/v1"]
)
def test_given_unsafe_azure_endpoint_when_constructing_then_rejected(base_url: str) -> None:
    from evaluation.vlm_judge.backend import OpenAICompatibleBackend

    with pytest.raises(ValueError, match="HTTPS without URL credentials"):
        OpenAICompatibleBackend(model="example", base_url=base_url)


@pytest.fixture
def saved_input_resolver(tmp_path: Path) -> ViewerDatasetResolver:
    from physical_ai_dataset_io.lerobot import EpisodeRecord

    from src.api.services.label_storage import LocalLabelStorage

    record = EpisodeRecord(
        "dataset/0",
        0,
        "Move the cube",
        30,
        30,
        {"front": tmp_path / "front.mp4", "wrist": tmp_path / "wrist.mp4"},
        None,
        None,
        video_windows={"front": (0.0, 1.0), "wrist": (1.0, 2.0)},
        media_identity={"front": "front-v1", "wrist": "wrist-v1"},
    )
    datasets = SimpleNamespace(
        get_dataset=AsyncMock(return_value=object()),
        get_source_revision=AsyncMock(return_value=("source", "revision")),
        get_episode_media_record=AsyncMock(return_value=record),
        list_episodes=AsyncMock(return_value=[SimpleNamespace(index=index) for index in [3, 1, 3]]),
    )
    annotations = SimpleNamespace(
        get_annotation_versioned=AsyncMock(return_value=SimpleNamespace(value=None, etag=None)),
        get_saved_edits=AsyncMock(return_value=SimpleNamespace(value=None, etag=None)),
    )
    return ViewerDatasetResolver(datasets, annotations, label_storage_factory=lambda: LocalLabelStorage(tmp_path))


async def test_given_duplicate_episode_indices_when_selected_then_sorted_and_deduplicated(
    saved_input_resolver: ViewerDatasetResolver,
) -> None:
    assert await saved_input_resolver.episode_indices("dataset", principal_scope_id="human") == [1, 3]


@pytest.mark.parametrize("operation", ["resolve", "episode_indices"])
async def test_given_missing_dataset_when_resolving_then_not_found(
    saved_input_resolver: ViewerDatasetResolver,
    operation: str,
) -> None:
    saved_input_resolver.datasets.get_dataset.return_value = None
    arguments = ("dataset", 0) if operation == "resolve" else ("dataset",)

    with pytest.raises(SavedInputError, match="Dataset not found") as error:
        await getattr(saved_input_resolver, operation)(*arguments, principal_scope_id="human")

    assert error.value.status_code == 404


async def test_given_oversized_selection_when_selected_then_rejected(
    saved_input_resolver: ViewerDatasetResolver,
) -> None:
    saved_input_resolver.datasets.list_episodes.return_value = [SimpleNamespace(index=0)] * 10001

    with pytest.raises(SavedInputError, match="at most 10000") as error:
        await saved_input_resolver.episode_indices("dataset", principal_scope_id="human")

    assert error.value.status_code == 422


async def test_given_missing_episode_when_resolving_then_not_found(
    saved_input_resolver: ViewerDatasetResolver,
) -> None:
    saved_input_resolver.datasets.get_episode_media_record.return_value = None

    with pytest.raises(SavedInputError, match="Episode not found") as error:
        await saved_input_resolver.resolve("dataset", 0, principal_scope_id="human")

    assert error.value.status_code == 404


@pytest.mark.parametrize("change", ["missing", "identity", "windows", "instruction"])
async def test_given_media_changed_during_resolution_when_resolved_then_rejected(
    saved_input_resolver: ViewerDatasetResolver,
    change: str,
) -> None:
    original = saved_input_resolver.datasets.get_episode_media_record.return_value
    replacements = {
        "missing": None,
        "identity": replace(original, media_identity={"front": "replacement"}),
        "windows": replace(original, video_windows={"front": (2.0, 3.0)}),
        "instruction": replace(original, instruction="Replacement instruction"),
    }
    saved_input_resolver.datasets.get_episode_media_record.side_effect = [original, replacements[change]]

    with pytest.raises(SavedInputError, match="Media changed"):
        await saved_input_resolver.resolve("dataset", 0, principal_scope_id="human")


@pytest.mark.parametrize("has_identity", [True, False])
async def test_given_selected_camera_when_resolved_then_only_selected_media_returned(
    saved_input_resolver: ViewerDatasetResolver,
    has_identity: bool,
) -> None:
    original = saved_input_resolver.datasets.get_episode_media_record.return_value
    if not has_identity:
        original = replace(original, media_identity=None)
        saved_input_resolver.datasets.get_episode_media_record.return_value = original

    record, snapshot = await saved_input_resolver.resolve(
        "dataset",
        0,
        principal_scope_id="human",
        views=("wrist",),
    )

    assert record.video_paths == {"wrist": original.video_paths["wrist"]}
    assert record.video_windows == {"wrist": (1.0, 2.0)}
    assert record.media_identity == ({"wrist": "wrist-v1"} if has_identity else None)
    assert record.episode_id == "dataset/episode_000000"
    assert record.snapshot_id == snapshot.snapshot_id
    assert snapshot.instruction == "Move the cube"


async def test_given_unknown_camera_when_resolved_then_rejected(
    saved_input_resolver: ViewerDatasetResolver,
) -> None:
    with pytest.raises(SavedInputError, match="Unknown camera") as error:
        await saved_input_resolver.resolve("dataset", 0, principal_scope_id="human", views=("missing",))

    assert error.value.status_code == 422


async def test_given_incomplete_sample_reference_when_validated_then_rejected(
    saved_input_resolver: ViewerDatasetResolver,
) -> None:
    with pytest.raises(SavedInputError):
        await saved_input_resolver.validation_sample("dataset", 0, principal_scope_id="human", reference={})


@pytest.mark.parametrize("changed", [False, True])
async def test_given_saved_label_sample_when_validated_then_requires_unchanged_baseline(
    saved_input_resolver: ViewerDatasetResolver,
    monkeypatch: pytest.MonkeyPatch,
    changed: bool,
) -> None:
    from physical_ai_curation.models import DatasetLabelsFile
    from physical_ai_curation.saved_input import label_baseline

    labels = DatasetLabelsFile(dataset_id="dataset", episodes={"0": ["success"]})
    replacement = DatasetLabelsFile(dataset_id="dataset", episodes={"0": ["failure"]}) if changed else labels
    storage = SimpleNamespace(load=AsyncMock(side_effect=[labels, replacement]))
    saved_input_resolver = ViewerDatasetResolver(
        saved_input_resolver.datasets, saved_input_resolver.annotations, label_storage_factory=lambda: storage
    )
    monkeypatch.setattr(
        "src.api.routers.labels._get_label_storage", lambda: pytest.fail("Resolver must use injected storage")
    )
    _, snapshot = await saved_input_resolver.resolve("dataset", 0, principal_scope_id="human")
    reference = {"snapshot_id": snapshot.snapshot_id, "labels_revision": label_baseline(labels, 0)["revision"]}

    if changed:
        with pytest.raises(SavedInputError, match="Validation label revision changed"):
            await saved_input_resolver.validation_sample("dataset", 0, principal_scope_id="human", reference=reference)
    else:
        sample = await saved_input_resolver.validation_sample(
            "dataset",
            0,
            principal_scope_id="human",
            reference=reference,
        )
        assert sample["human_outcome"] == "success"
        assert sample["baseline_source"] == "label"
        assert sample["reference"] == reference


async def test_given_saved_annotation_sample_when_validated_then_returns_saved_outcome(
    saved_input_resolver: ViewerDatasetResolver,
) -> None:
    from src.api.models.annotations import EpisodeAnnotationFile

    annotation = EpisodeAnnotationFile.model_validate(
        {
            "dataset_id": "dataset",
            "episode_index": 0,
            "annotations": [
                {
                    "annotator_id": "human",
                    "language_instruction": {"instruction": "Move the cube", "source": "human"},
                    "task_completeness": {"rating": "success", "confidence": 4},
                    "timestamp": "2026-01-01T00:00:00Z",
                    "trajectory_quality": {
                        "overall_score": 4,
                        "metrics": {
                            "smoothness": 4,
                            "efficiency": 4,
                            "safety": 4,
                            "precision": 4,
                        },
                    },
                    "data_quality": {"overall_quality": "good"},
                    "anomalies": {"anomalies": []},
                }
            ],
        }
    )
    saved_input_resolver.annotations.get_annotation_versioned.return_value = SimpleNamespace(
        value=annotation,
        etag='"annotation-v1"',
    )
    _, snapshot = await saved_input_resolver.resolve("dataset", 0, principal_scope_id="human")
    reference = {
        "snapshot_id": snapshot.snapshot_id,
        "annotation_author_id": "human",
        "annotation_revision": '"annotation-v1"',
    }

    sample = await saved_input_resolver.validation_sample("dataset", 0, principal_scope_id="human", reference=reference)

    assert sample["human_outcome"] == "success"
    assert sample["input"]["instruction"] == "Move the cube"


@pytest.mark.parametrize("failure", ["episode", "changed", "unknown-view", "source", "storage"])
async def test_given_invalid_saved_judge_input_when_router_resolves_then_safe_status(
    saved_input_resolver: ViewerDatasetResolver,
    failure: str,
) -> None:
    from fastapi import HTTPException

    from src.api.routers.vlm_judge import _resolve_saved_episode
    from src.api.storage import StorageError

    datasets = saved_input_resolver.datasets
    if failure == "episode":
        datasets.get_episode_media_record.return_value = None
    elif failure == "changed":
        original = datasets.get_episode_media_record.return_value
        datasets.get_episode_media_record.side_effect = [original, replace(original, instruction="Changed")]
    elif failure == "source":
        datasets.get_source_revision.side_effect = ValueError("private-source-detail")
    elif failure == "storage":
        datasets.get_episode_media_record.side_effect = StorageError("private-storage-detail")

    with pytest.raises(HTTPException) as error:
        await _resolve_saved_episode(
            datasets,
            saved_input_resolver.annotations,
            "dataset",
            0,
            "human",
            views=("missing",) if failure == "unknown-view" else (),
        )

    assert (
        error.value.status_code
        == {"episode": 404, "changed": 409, "unknown-view": 422, "source": 409, "storage": 503}[failure]
    )
    assert "private-" not in str(error.value.detail)


@pytest.mark.parametrize("identity", [True, False])
async def test_given_selected_judge_view_when_router_resolves_then_media_filtered(
    saved_input_resolver: ViewerDatasetResolver,
    identity: bool,
) -> None:
    from src.api.routers.vlm_judge import _resolve_saved_episode

    if not identity:
        datasets = saved_input_resolver.datasets
        datasets.get_episode_media_record.return_value = replace(
            datasets.get_episode_media_record.return_value, media_identity=None
        )

    record, snapshot = await _resolve_saved_episode(
        saved_input_resolver.datasets,
        saved_input_resolver.annotations,
        "dataset",
        0,
        "human",
        views=("front",),
    )

    assert list(record.video_paths) == ["front"]
    assert record.video_windows == {"front": (0.0, 1.0)}
    assert record.media_identity == ({"front": "front-v1"} if identity else None)
    assert snapshot.instruction == record.instruction


def test_given_disabled_judge_when_preparing_configuration_then_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    monkeypatch.setattr(vjs, "get_app_config", lambda: _config(monkeypatch, enabled=False))

    with pytest.raises(HTTPException) as error:
        vjs.prepare_job_configuration({})

    assert error.value.status_code == 503


async def test_given_azure_without_provider_when_creating_judge_jobs_then_unavailable(
    saved_input_resolver: ViewerDatasetResolver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    config = replace(_config(monkeypatch, enabled=True), storage_backend="azure")
    saved_input_resolver.datasets._blob_provider = None

    with pytest.raises(HTTPException) as error:
        await vjs.create_judge_jobs(config, saved_input_resolver.datasets, saved_input_resolver.annotations)

    assert error.value.status_code == 503


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


def _write_cli_metadata(root: Path, instruction: str, indices: tuple[int, ...]) -> None:
    (root / "meta").mkdir()
    (root / "meta" / "info.json").write_text(
        json.dumps(
            {
                "codebase_version": "v2.1",
                "fps": 30,
                "total_episodes": len(indices),
                "features": {"front": {"dtype": "video"}},
                "data_path": "data/{episode_index}.parquet",
                "video_path": "episode_{episode_index}.mp4",
            }
        )
    )
    (root / "meta" / "episodes.jsonl").write_text(
        "".join(json.dumps({"episode_index": index, "length": 30, "tasks": [instruction]}) + "\n" for index in indices)
    )
    for index in indices:
        (root / f"episode_{index}.mp4").write_bytes(b"synthetic media; no decoding in this test")


@pytest.mark.parametrize("override", ["", "Declared experiment instruction"])
def test_cli_persists_declared_instruction_before_service_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: str
) -> None:
    from evaluation.vlm_judge import run as inference_run
    from evaluation.vlm_judge.service import JudgeService
    from physical_ai_curation import run as judge_run

    _write_cli_metadata(tmp_path, "Saved dataset instruction", (0,))
    output = tmp_path / "results.jsonl"
    observed = []

    def build_with_declared_configuration(args: argparse.Namespace) -> JudgeService:
        declared = json.loads(output.with_suffix(".jsonl.config.json").read_text())
        assert declared["instruction_override"] == (override or None)
        assert declared["instruction_origin"] == ("cli-override" if override else "saved-input")
        assert declared["backend"] == "echo"
        assert "api_key" not in declared
        assert "base_url" not in declared
        assert declared["saved_inputs"][0]["instruction"] == (override or "Saved dataset instruction")
        observed.append(args.backend)
        raise ValueError("Synthetic stop before inference")

    monkeypatch.setattr(inference_run, "build_service", build_with_declared_configuration)
    assert (
        judge_run.main(
            [
                "--dataset",
                str(tmp_path),
                "--output",
                str(output),
                "--backend",
                "echo",
                "--single",
                "--instruction",
                override,
                "--api-key",
                "synthetic-not-a-credential",
            ]
        )
        == 2
    )
    assert observed == ["echo"]
    assert not output.exists()


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

    with pytest.raises(ValueError, match=r"result|Result|milestone"):
        _result_from_dict(payload)


def test_given_missing_result_when_decoded_then_validation_error() -> None:
    from evaluation.vlm_judge.service import _result_from_dict

    with pytest.raises(ValueError, match=r"result|Result"):
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
    from physical_ai_curation import run as judge_run
    from physical_ai_dataset_io.lerobot import EpisodeRecord

    _write_cli_metadata(tmp_path, "Saved instruction", (3, 1005))

    def process(*, episode: EpisodeRecord, **kwargs: object) -> dict[str, object]:
        if episode.episode_index == 1005:
            raise ValueError("Synthetic execution error")
        return _judge_payload()

    monkeypatch.setattr(judge_run, "_process_episode", process)

    assert (
        judge_run.main(
            ["--dataset", str(tmp_path), "--output", str(tmp_path / "results.jsonl"), "--backend", "echo", "--dry-run"]
        )
        == 1
    )
    assert len((tmp_path / "results.jsonl").read_text().splitlines()) == 1


@pytest.mark.asyncio
async def test_given_saved_local_instruction_when_resolved_then_author_and_revision_bound(tmp_path: Path) -> None:
    from physical_ai_curation.adapters.local_saved_input import LocalSavedInputReader
    from physical_ai_curation.saved_input import resolve_saved_input

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

    from physical_ai_curation.adapters.local_saved_input import LocalSavedInputReader
    from physical_ai_curation.saved_input import resolve_saved_input

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
    from physical_ai_curation import run as judge_run

    _write_cli_metadata(tmp_path, "Original instruction", (3,))
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
    from evaluation.vlm_judge.service import BackendConfig, JudgeService, ServiceConfig
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from physical_ai_curation.api import build_router

    app = FastAPI()
    app.include_router(build_router(JudgeService(ServiceConfig(backend=BackendConfig(kind="echo")))))
    with TestClient(app) as client:
        response = client.post("/judge", json={"dataset_id": "synthetic", "episode_index": 3})
    assert response.status_code == 503
