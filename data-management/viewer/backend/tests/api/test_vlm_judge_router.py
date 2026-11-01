"""Tests for the VLM-as-judge router.

These tests run the full FastAPI app with the echo backend so no GPU or
network is required. They drive the real ``evaluation.vlm_judge`` package
end-to-end (frame extraction, agent, cache) against a synthetic LeRobot v2.1
dataset generated on the fly, so they are self-contained and run in CI without
any pre-downloaded dataset.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import shutil
import subprocess
import sys
import sysconfig
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import av
import fastapi
import numpy as np
import physical_ai_curation.api as api_module
import physical_ai_dataset_io.lerobot as dataset_module
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from evaluation.vlm_judge.judge import JudgeResult
from evaluation.vlm_judge.prompts import PROMPT_VERSION
from evaluation.vlm_judge.service import BackendConfig, FrameConfig, JudgeService, ServiceConfig
from fastapi.testclient import TestClient
from physical_ai_curation import run as curation_run
from physical_ai_curation.adapters.job_storage import LocalJobStore
from physical_ai_curation.adapters.local_dataset import LocalDatasetResolver
from physical_ai_dataset_io.media import MediaInputError, validate_video_path

pytest_plugins = ["evaluation.tests.vlm_judge.conftest"]

DATASET_ID = "synthetic-eval"
INSTRUCTION = "Pick up the cube"


@pytest.mark.parametrize("first_module", ["judge_port", "evidence"])
def test_given_curation_contracts_when_imported_in_either_order_then_types_resolve(first_module: str) -> None:
    script = """
import importlib
import sys
from typing import get_type_hints

importlib.import_module(f"physical_ai_curation.{sys.argv[1]}")
if sys.argv[1] == "judge_port":
    assert "physical_ai_curation.evidence" not in sys.modules
from physical_ai_curation import evidence, judge_port

assert evidence.EvidenceRecord is judge_port.EvidenceRecord
assert get_type_hints(judge_port.JudgePort.evaluate)["return"] is evidence.EvidenceRecord
assert get_type_hints(evidence.bind_evidence)["invocation"] is judge_port.JudgeInvocation
"""
    result = subprocess.run(
        [sys.executable, "-c", script, first_module],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["preview", "dry-run", "score"])
def test_given_viewer_dataset_when_using_inference_cli_then_scores_without_curation(
    jsonl_dataset: Path, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    from evaluation.vlm_judge import run as inference_run

    output = jsonl_dataset / "inference.jsonl"
    arguments = [
        "--dataset",
        str(jsonl_dataset),
        "--output",
        str(output),
        "--backend",
        "echo",
        "--cache-dir",
        "",
        "--n-frames",
        "3",
        "--indices",
        "0-0,0",
        "--instruction",
        INSTRUCTION,
    ]
    if mode != "score":
        arguments.append("--config-preview" if mode == "preview" else "--dry-run")

    assert inference_run.main(arguments) == 0

    if mode == "preview":
        assert json.loads(capsys.readouterr().out)["backend"] == "echo"
        assert not output.exists()
    else:
        row = json.loads(output.read_text())
        assert row["instruction"] == INSTRUCTION
        assert row["episode_id"].endswith("episode_000000")
        assert row.get("dry_run") is True if mode == "dry-run" else row["outcome_success"] is True
        declaration = json.loads(output.with_suffix(".jsonl.config.json").read_text())
        assert declaration["instruction_origin"] == "cli-override"
        assert declaration["episode_indices"] == [0]
    assert not (jsonl_dataset.parent / ".curation").exists()


@pytest.mark.parametrize("mode", ["preview", "dry-run", "score"])
def test_given_rollout_media_when_using_inference_cli_then_preserves_episode_and_instruction(
    rollout_video: Path, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    from evaluation.vlm_judge import policy_eval

    root = rollout_video.parent
    output = root / "rollout-results.jsonl"
    overrides = root / "instructions.json"
    overrides.write_text(json.dumps({"rollout": INSTRUCTION}))
    arguments = [
        "--rollout-root",
        str(root),
        str(root / "missing"),
        str(root),
        "--instructions-file",
        str(overrides),
        "--output",
        str(output),
        "--backend",
        "echo",
        "--cache-dir",
        "",
        "--n-frames",
        "3",
        "--limit",
        "1",
    ]
    if mode != "score":
        arguments.append("--config-preview" if mode == "preview" else "--dry-run")

    assert policy_eval.main(arguments) == 0

    if mode == "preview":
        assert json.loads(capsys.readouterr().out)["backend"] == "echo"
        assert not output.exists()
    else:
        row = json.loads(output.read_text())
        assert row["episode_id"] == "rollout"
        assert row["instruction"] == INSTRUCTION
        assert row["video_path"] == str(rollout_video)
        assert row.get("dry_run") is True if mode == "dry-run" else row["outcome_success"] is True
    assert not (root / ".curation").exists()


def test_given_echo_shim_when_using_multimodal_api_then_returns_documented_completion() -> None:
    import base64
    import io

    from evaluation.vlm_judge.openai_shim import build_echo_app
    from PIL import Image

    image = io.BytesIO()
    Image.new("RGB", (12, 12), "red").save(image, "PNG")
    image_url = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode("ascii")
    with TestClient(build_echo_app()) as shim:
        assert shim.get("/health").json() == {"status": "ok", "model": "echo"}
        assert shim.get("/v1/models").json()["data"][0]["id"] == "echo"
        assert shim.get("/docs").status_code == 200
        assert shim.get("/redoc").status_code == 200
        response = shim.post(
            "/v1/chat/completions",
            json={
                "model": "echo",
                "messages": [
                    {"role": "system", "content": "Observe the frames"},
                    {"role": "assistant", "content": "Previous observation"},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe progress"},
                            {"type": "image_url", "image_url": {"url": image_url}},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    },
                ],
            },
        )

    assert response.status_code == 200
    assert response.json()["model"] == "echo"
    assert response.json()["choices"][0]["message"]["content"] == "[0, 100]"


@pytest.mark.parametrize("request_kind", ["stream", "file-image", "remote-image"])
def test_given_unsupported_shim_request_when_completing_then_rejects_without_network(
    request_kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evaluation.vlm_judge import openai_shim

    monkeypatch.setattr(openai_shim, "_ALLOW_REMOTE_IMAGES", False)
    request = (
        {"messages": [], "stream": True}
        if request_kind == "stream"
        else {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "file:///example.png"
                                if request_kind == "file-image"
                                else "https://example.invalid/image.png",
                            },
                        }
                    ],
                }
            ],
        }
    )

    with TestClient(openai_shim.build_echo_app()) as shim:
        response = shim.post("/v1/chat/completions", json=request)

    assert response.status_code == 400


def test_given_judge_cli_when_detached_then_same_job_resumes_with_saved_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from physical_ai_curation import run as judge_run

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
    from physical_ai_curation import run as judge_run
    from physical_ai_curation.adapters.local_dataset import LocalDatasetResolver
    from physical_ai_curation.models import ContributionLedger

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


@pytest.mark.parametrize("selected_view", [False, True])
def test_viewer_durable_job_publishes_canonical_evidence_without_labels(
    tmp_path: Path,
    restore_default_app: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    selected_view: bool,
) -> None:
    from physical_ai_curation import run as judge_run
    from physical_ai_curation.models import DatasetLabelsFile

    _build_dataset(tmp_path / DATASET_ID, instruction=INSTRUCTION)
    restore_default_app.setenv("VLM_JUDGE_BASE_URL", "")
    client = _reload_app(restore_default_app, tmp_path)
    views = [dataset_module.load_dataset_spec(tmp_path / DATASET_ID).video_keys[0]] if selected_view else []
    response = client.post(
        f"/api/datasets/{DATASET_ID}/episodes/0/judge",
        json={"views": views},
        headers={"Idempotency-Key": "durable-request"},
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    viewer_job = asyncio.run(client.app.state.judge_jobs.store.read())["jobs"][response.json()["id"]]
    arguments = [
        "--dataset",
        str(tmp_path / DATASET_ID),
        "--dataset-id",
        DATASET_ID,
        "--principal-scope-id",
        viewer_job["actor"],
        "--job-dir",
        str(tmp_path / "cli-jobs"),
        "--output",
        str(tmp_path / "cli-results.jsonl"),
        "--backend",
        "echo",
        "--n-frames",
        "6",
        "--cache-dir",
        "",
    ]
    if views:
        arguments.extend(["--views", *views])
    assert judge_run.main([*arguments, "--dry-run"]) == 0
    declaration = json.loads((tmp_path / "cli-results.jsonl.config.json").read_text())
    assert declaration["saved_inputs"][0] == viewer_job["targets"][0]["input"]
    assert judge_run.main([*arguments, "--single", "--detach", "--request-id", "cli-parity"]) == 0
    cli_accepted = json.loads(capsys.readouterr().out)
    assert judge_run.main([*arguments, "--operation", "status", "--job-id", cli_accepted["id"]]) == 0
    cli_status = json.loads(capsys.readouterr().out)
    assert cli_status["targets"][0]["input"] == viewer_job["targets"][0]["input"]
    assert cli_status["config"] == viewer_job["config"]
    assert cli_status["config_revision"] == viewer_job["config_revision"]
    assert cli_status["status"] == "queued" and cli_status["judged"] == 0
    assert not (tmp_path / DATASET_ID / "meta" / "episode_labels.json").exists()
    assert asyncio.run(client.app.state.judge_jobs.run_once())
    completed = client.get(response.headers["Location"])
    assert completed.json()["status"] == "succeeded", completed.text
    assert completed.json()["judged"] == 1
    assert completed.json()["applied"] == 0
    evidence = client.get("/api/judge/results", params={"dataset_id": DATASET_ID, "episode_index": 0})
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()["items"][0]["result"]["instruction"] == INSTRUCTION
    assert evidence.json()["items"][0]["applicability"] == "current"
    assert judge_run.main([*arguments, "--single", "--request-id", "cli-parity"]) == 0
    cli_completed = json.loads(capsys.readouterr().out)
    assert cli_completed["status"] == "succeeded"
    assert cli_completed["targets"][0]["result"] == evidence.json()["items"][0]["result"]
    assert not (tmp_path / DATASET_ID / "meta" / "episode_labels.json").exists()
    apply_path = f"/api/judge/jobs/{response.json()['id']}/apply"
    browser_body = json.dumps({"episode_indices": [0]})
    undeclared = client.post(apply_path, content=browser_body, headers={"Content-Type": "text/plain;charset=UTF-8"})
    assert undeclared.status_code == 422
    assert "model_attributes_type" in undeclared.text
    assert not (tmp_path / DATASET_ID / "meta" / "episode_labels.json").exists()
    labels_path = tmp_path / DATASET_ID / "meta" / "episode_labels.json"
    human_labels = DatasetLabelsFile.model_validate(
        {"dataset_id": DATASET_ID, "episodes": {"0": ["HUMAN"]}, "analysis": {"0": {"object": "human object"}}}
    )
    labels_path.write_text(human_labels.model_dump_json())
    application = client.post(apply_path, content=browser_body, headers={"Content-Type": "application/json"})
    assert application.status_code == 202, application.text
    assert asyncio.run(client.app.state.judge_jobs.run_once())
    applied = client.get(response.headers["Location"]).json()
    assert applied["applied"] == 1
    assert applied["judged"] == 1
    reloaded = client.get("/api/judge/results", params={"dataset_id": DATASET_ID, "episode_index": 0}).json()
    assert [item["run_id"] for item in reloaded["items"]] == [response.json()["id"]]
    assert reloaded["items"][0]["result_id"] == evidence.json()["items"][0]["result_id"]
    assert set(client.get(f"/api/datasets/{DATASET_ID}/labels").json()["episodes"]["0"]) == {"HUMAN", "SUCCESS"}
    saved = json.loads((tmp_path / DATASET_ID / "meta" / "episode_labels.json").read_text())
    assert set(saved["episodes"]["0"]) == {"HUMAN", "SUCCESS"}
    assert saved["analysis"]["0"]["object"] == "human object"
    assert all(
        item["machine"]["run_id"] == response.json()["id"]
        for item in saved["provenance"]["0"]["contributions"]
        if item["origin"] == "machine"
    )
    assert judge_run.main([*arguments, "--operation", "apply", "--job-id", cli_accepted["id"]]) == 0
    assert json.loads(capsys.readouterr().out)["applied"] == 1
    reapplied = json.loads(labels_path.read_text())
    assert set(reapplied["episodes"]["0"]) == {"HUMAN", "SUCCESS"}
    assert reapplied["analysis"]["0"]["object"] == "human object"


def test_viewer_recovers_accepted_job_on_startup_without_browser(
    tmp_path: Path,
    restore_default_app: pytest.MonkeyPatch,
) -> None:
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
    from evaluation.vlm_judge.service import BackendConfig, FrameConfig, JudgeService, ServiceConfig
    from fastapi import FastAPI
    from physical_ai_curation.adapters.job_storage import LocalJobStore
    from physical_ai_curation.adapters.local_dataset import LocalDatasetResolver

    _build_dataset(tmp_path, instruction=INSTRUCTION)
    service = JudgeService(ServiceConfig(backend=BackendConfig(kind="echo"), frames=FrameConfig(n_frames=4)))
    app = FastAPI()
    app.include_router(
        api_module.build_router(
            service,
            resolver=LocalDatasetResolver({DATASET_ID: tmp_path}),
            local_actor="local-test",
            job_store=LocalJobStore(tmp_path / "jobs"),
            allow_unauthenticated=True,
        )
    )
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
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
    record = next(dataset_module.iter_episodes(tmp_path))
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


@pytest.mark.parametrize("enabled", ["false", "true"])
def test_backend_starts_without_optional_evaluator(tmp_path: Path, enabled: str) -> None:
    app_dir = tmp_path / "app"
    shutil.copytree(Path(__file__).resolve().parents[2] / "src", app_dir / "src")
    shutil.copytree(Path(api_module.__file__).parent, app_dir / "physical_ai_curation")
    import physical_ai_dataset_io

    shutil.copytree(Path(physical_ai_dataset_io.__file__).parent, app_dir / "physical_ai_dataset_io")
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
import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, sys.argv[1])
sys.path.append(sys.argv[2])
sys.modules["evaluation"] = None
assert importlib.util.find_spec("evaluation") is None

from fastapi.testclient import TestClient
from src.api.main import app
from src.api.config import get_app_config
from src.api.services.vlm_judge_service import get_vlm_judge_service
from src.api.services.label_storage import LocalLabelStorage
from src.api.storage import RevisionConflictError
from src.api.storage.local import LocalStorageAdapter
from src.api.models.annotations import EpisodeAnnotationFile
from physical_ai_curation.models import DatasetLabelsFile

async def verify_curation():
    storage = LocalLabelStorage(os.environ["DATA_DIR"])
    labels = DatasetLabelsFile(dataset_id="test")
    revision = await storage.save("test", labels, if_none_match=True)
    saved = await storage.load_versioned("test")
    assert saved.value == labels and saved.etag == revision
    try:
        await storage.save("test", labels, if_match='"stale"')
    except RevisionConflictError:
        pass
    else:
        raise AssertionError("Stale conditional write was accepted")
    assert (await storage.load_versioned("test")).etag == revision

    annotations = LocalStorageAdapter(os.environ["DATA_DIR"])
    annotation = EpisodeAnnotationFile.model_validate({
        "dataset_id": "test", "episode_index": 0,
        "annotations": [{
            "annotator_id": "local-user", "timestamp": "2026-10-15T00:00:00Z",
            "task_completeness": {"rating": "success", "confidence": 5, "completion_percentage": 100},
            "trajectory_quality": {
                "overall_score": 5,
                "metrics": {"smoothness": 5, "efficiency": 5, "safety": 5, "precision": 5},
            },
            "data_quality": {"overall_quality": "good"}, "anomalies": {},
            "notes": "Saved without evaluator",
        }],
    })
    annotation_revision = await annotations.save_annotation("test", 0, annotation, if_none_match=True)
    saved_annotation = await annotations.get_annotation_versioned("test", 0)
    assert saved_annotation.value == annotation and saved_annotation.etag == annotation_revision
    try:
        await annotations.save_annotation("test", 0, annotation, if_match='"stale"')
    except RevisionConflictError:
        pass
    else:
        raise AssertionError("Stale annotation write was accepted")
    assert (await annotations.get_annotation_versioned("test", 0)).etag == annotation_revision

with TestClient(app) as client:
    assert client.get("/health").status_code == 200
    assert get_vlm_judge_service(get_app_config()) is None
    if os.environ["VLM_JUDGE_ENABLED"] == "true":
        assert client.get("/api/judge/jobs?dataset_id=test").json()["items"] == []
        assert client.get("/api/judge/approvals?dataset_id=test").json()["items"] == []
        token = client.get("/api/csrf-token").json()["csrf_token"]
        response = client.post(
            "/api/judge/jobs", json={"dataset_id": "test", "episode_indices": [0]},
            headers={"X-CSRF-Token": token, "Idempotency-Key": "unavailable"},
        )
        assert response.status_code == 503, response.text
        assert client.get("/api/judge/jobs?dataset_id=test").json()["items"] == []
    else:
        assert not hasattr(app.state, "judge_jobs")
    asyncio.run(verify_curation())
    from physical_ai_curation.api import build_router
    from physical_ai_curation import run, label_dataset
    assert run.main(["--dataset", os.environ["DATA_DIR"], "--operation", "list"]) == 0
    assert label_dataset.main(["--dataset-root", os.environ["DATA_DIR"], "--operation", "list"]) == 0
    assert importlib.util.find_spec("evaluation") is None
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


def _build_dataset(root: Path, *, instruction: str | None, n_frames: int = 12, n_episodes: int = 1) -> None:
    """Materialize a minimal LeRobot v2.1 dataset at ``root``."""
    (root / "meta").mkdir(parents=True, exist_ok=True)
    for episode_index in range(n_episodes):
        data_path = root / "data" / "chunk-000" / f"episode_{episode_index:06d}.parquet"
        data_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.table(
                {
                    "frame_index": list(range(n_frames)),
                    "episode_index": [episode_index] * n_frames,
                    "timestamp": np.arange(n_frames) / 30,
                    "observation.state": [[0.0]] * n_frames,
                    "action": [[0.0]] * n_frames,
                }
            ),
            data_path,
        )
        _write_mp4(root / "videos" / "chunk-000" / "obs.front" / f"episode_{episode_index:06d}.mp4", n_frames=n_frames)
    (root / "meta" / "info.json").write_text(
        json.dumps(
            {
                "codebase_version": "v2.1",
                "fps": 30,
                "total_episodes": n_episodes,
                "chunks_size": 1000,
                "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
                "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
                "features": {"obs.front": {"dtype": "video", "shape": [48, 64, 3], "names": ["h", "w", "c"]}},
            },
        ),
    )
    (root / "meta" / "tasks.jsonl").write_text("")
    (root / "meta" / "episodes.jsonl").write_text(
        "".join(
            json.dumps({"episode_index": index, "length": n_frames, "tasks": [instruction] if instruction else []})
            + "\n"
            for index in range(n_episodes)
        )
    )


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
    import src.api.services.vlm_judge_service as service_mod

    config_mod._app_config = None
    ann_service_mod._annotation_service = None
    ds_service_mod._dataset_service = None
    service_mod.reset_vlm_judge_service()

    from src.api.routers import labels

    labels._label_storage = None

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
    import src.api.services.vlm_judge_service as service_mod

    config_mod._app_config = None
    ann_service_mod._annotation_service = None
    ds_service_mod._dataset_service = None
    service_mod.reset_vlm_judge_service()

    from src.api.routers import labels

    labels._label_storage = None

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


def test_backend_and_pin_changes_invalidate_service_and_viewer_cache(
    vlm_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evaluation.vlm_judge.backend import EchoBackend
    from evaluation.vlm_judge.service import JudgeService

    import src.api.routers.vlm_judge as router_mod
    import src.api.services.vlm_judge_service as service_mod
    from src.api.config import get_app_config

    original = service_mod.get_vlm_judge_service(get_app_config())
    assert original is not None
    base = original.config
    calls = []

    def fake_backend(config: object) -> EchoBackend:
        calls.append(config)
        backend = EchoBackend()
        backend.name = "echo" if config.kind == "echo" else config.model_id
        return backend

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
        monkeypatch.setattr(service_mod, "_service", service)
        if hasattr(vlm_client.app.state, "judge_jobs"):
            del vlm_client.app.state.judge_jobs
        before = vlm_client.get(path).json()
        assert before["cached"] is expected_cached
        response = vlm_client.post(path, json={})
        job = _finish_job(vlm_client, response)
        assert job["status"] == "succeeded"
        assert job["targets"][0]["cached"] is expected_cached
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

    assert _finish_job(client, rsp)["targets"][0]["result"]["instruction"] == "Dataset fallback instruction"


@pytest.mark.parametrize("views", [["obs.front"], None])
def test_post_accepts_safe_view_filter(vlm_client: TestClient, views: list[str] | None) -> None:
    path = f"/api/datasets/{DATASET_ID}/episodes/0/judge"
    rsp = vlm_client.post(
        path,
        json={"views": views, "force": True},
    )
    assert _finish_job(vlm_client, rsp)["targets"][0]["result"]["episode_id"] == f"{DATASET_ID}/episode_000000"


@pytest.mark.parametrize("failure", ["saved-input", "conflict"])
def test_given_submission_failure_when_posting_judge_then_returns_expected_conflict(
    vlm_client: TestClient, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from physical_ai_curation.jobs import JudgeJobs

    from src.api.services.saved_input_service import SavedInputError

    async def reject_submission(self: JudgeJobs, *args: Any, **kwargs: Any) -> None:
        if failure == "saved-input":
            raise SavedInputError("Saved snapshot changed", 412)
        raise ValueError("private-submission-details")

    monkeypatch.setattr(JudgeJobs, "submit", reject_submission)

    response = vlm_client.post(f"/api/datasets/{DATASET_ID}/episodes/0/judge", json={})

    assert response.status_code == (412 if failure == "saved-input" else 409)
    assert response.json() == {
        "detail": "Saved snapshot changed" if failure == "saved-input" else "Judge request conflicts with saved state"
    }
    assert "private-submission-details" not in response.text


@pytest.mark.parametrize("endpoint", ["judge", "judge/snapshot"])
@pytest.mark.parametrize("failure", ["source", "storage"])
def test_given_saved_input_failure_when_reading_judge_then_returns_sanitized_error(
    vlm_client: TestClient, monkeypatch: pytest.MonkeyPatch, endpoint: str, failure: str
) -> None:
    from src.api.services.dataset_service import DatasetService
    from src.api.storage import StorageError

    async def unavailable_source(self: DatasetService, dataset_id: str, episode_index: int) -> str:
        if failure == "source":
            raise ValueError("private-source-details")
        raise StorageError("private-storage-details")

    monkeypatch.setattr(DatasetService, "get_source_revision", unavailable_source)

    response = vlm_client.get(f"/api/datasets/{DATASET_ID}/episodes/0/{endpoint}")

    assert response.status_code == (409 if failure == "source" else 503)
    assert response.json() == {
        "detail": "Saved input source changed or is unavailable"
        if failure == "source"
        else "Saved input storage unavailable"
    }
    assert "private-source-details" not in response.text
    assert "private-storage-details" not in response.text


@pytest.mark.parametrize("change", ["removed", "instruction"])
def test_given_media_changes_during_resolution_when_reading_snapshot_then_rejects_stale_input(
    vlm_client: TestClient, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    from src.api.services.dataset_service import DatasetService

    original = DatasetService.get_episode_media_record
    reads = []

    async def changed_media(
        self: DatasetService, dataset_id: str, episode_index: int
    ) -> dataset_module.EpisodeRecord | None:
        record = await original(self, dataset_id, episode_index)
        reads.append(record)
        if len(reads) == 2:
            return None if change == "removed" else replace(record, instruction="Replacement instruction")
        return record

    monkeypatch.setattr(DatasetService, "get_episode_media_record", changed_media)

    response = vlm_client.get(f"/api/datasets/{DATASET_ID}/episodes/0/judge/snapshot")

    assert response.status_code == 409
    assert response.json() == {"detail": "Media changed while resolving saved inputs"}
    assert len(reads) == 2


def test_given_unknown_camera_when_submitting_judge_then_rejects_selection(vlm_client: TestClient) -> None:
    response = vlm_client.post(f"/api/datasets/{DATASET_ID}/episodes/0/judge", json={"views": ["obs.missing"]})

    assert response.status_code == 422
    assert response.json() == {"detail": "Unknown camera selection"}


@pytest.mark.parametrize("helper", ["_resolve_episode", "_dataset_root"])
def test_given_missing_local_root_when_resolving_judge_media_then_reports_unavailable(helper: str) -> None:
    from types import SimpleNamespace

    from src.api.routers import vlm_judge

    resolve = getattr(vlm_judge, helper)
    arguments = (
        (SimpleNamespace(base_path=None), DATASET_ID, 0)
        if helper == "_resolve_episode"
        else (
            SimpleNamespace(base_path=None),
            DATASET_ID,
        )
    )

    with pytest.raises(fastapi.HTTPException) as error:
        resolve(*arguments)

    assert error.value.status_code == 503
    assert error.value.detail == "VLM judge requires a local dataset path (base_path)"


@pytest.mark.parametrize("scenario", ["found", "missing-episode", "missing-dataset", "invalid-metadata"])
def test_given_local_dataset_when_resolving_judge_media_then_preserves_lookup_contract(
    tmp_path: Path, scenario: str
) -> None:
    from types import SimpleNamespace

    from src.api.routers import vlm_judge

    root = tmp_path / DATASET_ID
    if scenario != "missing-dataset":
        _build_dataset(root, instruction=INSTRUCTION)
    if scenario == "invalid-metadata":
        (root / "meta" / "info.json").write_text("{")
    service = SimpleNamespace(base_path=tmp_path)

    if scenario in ("missing-dataset", "invalid-metadata"):
        with pytest.raises(fastapi.HTTPException) as error:
            vlm_judge._resolve_episode(service, DATASET_ID, 0)
        assert error.value.status_code == (404 if scenario == "missing-dataset" else 400)
        if scenario == "invalid-metadata":
            assert error.value.detail == "Invalid media input"
    else:
        record = vlm_judge._resolve_episode(
            service, DATASET_ID, 7 if scenario == "missing-episode" else 0, views=("obs.front",)
        )
        if scenario == "missing-episode":
            assert record is None
        else:
            assert record.instruction == INSTRUCTION
            assert record.video_paths == {"obs.front": root / "videos/chunk-000/obs.front/episode_000000.mp4"}


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
    assert _finish_job(vlm_client, vlm_client.post(path, json={}))["status"] == "succeeded"
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

    assert response.status_code == (202 if method == "POST" else 200)
    if method == "POST":
        assert _finish_job(vlm_client, response)["status"] == "succeeded"
    first = vlm_client.post(path, json={})
    assert _finish_job(vlm_client, first)["targets"][0]["cached"] is (method == "POST")
    assert _finish_job(vlm_client, vlm_client.post(path, json={}))["targets"][0]["cached"] is True
    assert vlm_client.get(path).json()["cached"] is True


@pytest.mark.parametrize("indices", [None, [], [0], [1]])
@pytest.mark.parametrize("validate_media", [False, True])
def test_v3_jsonl_selection_preserves_trusted_and_validated_iteration(
    tmp_path: Path, indices: list[int] | None, validate_media: bool
) -> None:
    from physical_ai_dataset_io.media import validate_video_path

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

    records = list(
        dataset_module.iter_episodes(root, indices=indices, video_validator=validator if validate_media else None)
    )

    assert [record.episode_index for record in records] == ([0, 1] if indices is None else indices)
    expected_paths = {path for record in records for path in record.video_paths.values()}
    assert set(validated) == (expected_paths if validate_media else set())


def test_contained_v3_jsonl_episode_judges_and_reuses_cache(vlm_client: TestClient, tmp_path: Path) -> None:
    _set_video_template(tmp_path / DATASET_ID, "v3.0", "videos/chunk-000/obs.front/episode_{episode_index:06d}.mp4")
    path = f"/api/datasets/{DATASET_ID}/episodes/0/judge"

    first = vlm_client.post(path, json={})
    first_job = _finish_job(vlm_client, first)
    second = vlm_client.post(path, json={})
    second_job = _finish_job(vlm_client, second)

    assert first_job["status"] == second_job["status"] == "succeeded"
    assert first_job["targets"][0]["cached"] is False
    assert second_job["targets"][0]["cached"] is True
    assert vlm_client.get(path).json()["cached"] is True


@pytest.mark.parametrize("failure", ["corrupt", "window", "backend"])
def test_media_and_backend_failures_do_not_disclose_paths(
    vlm_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
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

    job = _finish_job(vlm_client, response)
    assert job["status"] == "failed"
    assert job["targets"][0]["error"] == ("MediaInputError" if failure == "corrupt" else "ValueError")
    assert str(video) not in json.dumps(job)
    assert "private backend details" not in json.dumps(job)


class StubService(JudgeService):
    """Lightweight stand-in for ``JudgeService`` that records calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

        from evaluation.vlm_judge.service import (
            BackendConfig,
            FrameConfig,
            ServiceConfig,
        )

        super().__init__(
            ServiceConfig(
                backend=BackendConfig(kind="echo", model_id="stub-model"),
                frames=FrameConfig(),
                cache_dir=None,
            )
        )

    def judge_episode(
        self,
        *,
        episode_id: str,
        instruction: str,
        video_paths: Mapping[str, Path | str],
        from_s: float | None = None,
        to_s: float | None = None,
        force: bool = False,
        **metadata: object,
    ) -> JudgeResult:
        self.calls.append(
            {
                "episode_id": episode_id,
                "instruction": instruction,
                "video_paths": dict(video_paths),
                "from_s": from_s,
                "to_s": to_s,
                "force": force,
            },
        )
        return JudgeResult(
            episode_id=episode_id,
            instruction=instruction,
            judge_model="echo",
            prompt_version=PROMPT_VERSION,
            n_frames=8,
            outcome_success=True,
            outcome_confidence=0.9,
            outcome_n_valid_votes=3,
            progress_per_frame=[0, 14, 28, 42, 57, 71, 85, 100],
            voc=0.95,
            milestones=[],
            failure_mode=None,
        )


def _write_dataset(root: Path, video_path: str, *, version: str = "v2.1") -> None:
    (root / "meta").mkdir(exist_ok=True)
    (root / "meta" / "info.json").write_text(
        json.dumps(
            {
                "codebase_version": version,
                "fps": 12,
                "total_episodes": 1,
                "features": {"front": {"dtype": "video", "videos_path": video_path}},
                "data_path": "data/{episode_index}.parquet",
                "video_path": video_path,
            }
        )
    )
    (root / "meta" / "episodes.jsonl").write_text(
        json.dumps({"episode_index": 0, "tasks": ["pick orange"], "length": 12}) + "\n"
    )


def _connection(root: Path, service: JudgeService | None, *, prefix: str = "") -> TestClient:
    app = fastapi.FastAPI()
    app.include_router(
        api_module.build_router(
            service,
            resolver=LocalDatasetResolver({"synthetic": root}),
            local_actor="local-test",
            job_store=LocalJobStore(root / ".judge-jobs"),
            allow_unauthenticated=True,
        ),
        prefix=prefix,
    )
    return TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))


@pytest.fixture
def client(tmp_path: Path) -> tuple[TestClient, StubService]:
    service = StubService()
    root = tmp_path / "media"
    root.mkdir()
    (root / "ep0.mp4").write_bytes(b"stub video")
    _write_dataset(root, "ep0.mp4")
    return _connection(root, service, prefix="/api/vlm-judge"), service


def test_given_unavailable_evaluator_when_managing_then_reads_work_and_scoring_is_unavailable(tmp_path: Path) -> None:
    _write_dataset(tmp_path, "ep0.mp4")
    (tmp_path / "ep0.mp4").write_bytes(b"stub video")
    with _connection(tmp_path, None) as connection:
        assert connection.get("/jobs", params={"dataset_id": "synthetic"}).json()["items"] == []
        assert connection.get("/episodes", params={"dataset_id": "synthetic"}).status_code == 200
        assert connection.get("/health").json()["status"] == "unavailable"
        response = connection.post("/judge", json={"dataset_id": "synthetic", "episode_index": 0})
        assert response.status_code == 503
        assert response.json() == {"detail": "Judge unavailable"}
        assert connection.get("/jobs", params={"dataset_id": "synthetic"}).json()["items"] == []
        assert connection.get("/jobs", headers={"origin": "https://other.invalid"}).status_code == 403


_LIFECYCLE_ENDPOINTS = [
    ("POST", "/judge"),
    ("POST", "/jobs"),
    ("GET", "/jobs?dataset_id=synthetic"),
    ("GET", "/jobs/example"),
    ("POST", "/jobs/example/cancel"),
    ("POST", "/jobs/example/retry"),
    ("POST", "/jobs/example/apply"),
    ("POST", "/jobs/example/approve"),
    ("GET", "/approvals?dataset_id=synthetic"),
    ("POST", "/resets/preview"),
    ("POST", "/resets"),
    ("GET", "/resets?dataset_id=synthetic"),
    ("POST", "/resets/retry"),
    ("GET", "/episodes?dataset_id=synthetic"),
    ("GET", "/results?dataset_id=synthetic&episode_index=0"),
]


@pytest.mark.parametrize(("method", "path"), _LIFECYCLE_ENDPOINTS)
def test_given_cross_origin_lifecycle_request_when_parsing_then_denies_before_dependencies(
    client: tuple[TestClient, StubService], method: str, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, service = client

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Denied lifecycle request reached dataset resolution")

    monkeypatch.setattr(LocalDatasetResolver, "resolve", forbidden)
    monkeypatch.setattr(LocalDatasetResolver, "episode_indices", forbidden)
    response = connection.request(
        method,
        f"/api/vlm-judge{path}",
        content='{"private":',
        headers={"content-type": "application/json", "origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Request forbidden"}
    assert not hasattr(connection.app.state, "judge_jobs")
    assert service.calls == []


@pytest.mark.parametrize("path", ["/jobs", "/episodes", "/approvals", "/results", "/resets"])
def test_given_local_get_without_content_type_when_reading_lifecycle_then_admits(
    client: tuple[TestClient, StubService], path: str
) -> None:
    connection, _ = client
    response = connection.get(f"/api/vlm-judge{path}", params={"dataset_id": "synthetic", "episode_index": 0})
    assert response.status_code == 200


@pytest.mark.parametrize("action", ["cancel", "retry"])
def test_given_local_bodyless_request_when_controlling_job_then_admits(
    client: tuple[TestClient, StubService], action: str
) -> None:
    connection, _ = client
    response = connection.post(f"/api/vlm-judge/jobs/missing/{action}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Judge job not found"}


class TestApi:
    def test_health_returns_model_id(self, client) -> None:
        c, _ = client
        rsp = c.get("/api/vlm-judge/health")
        assert rsp.status_code == 200
        body = rsp.json()
        assert body["status"] == "ok"
        assert body["model_id"] == "stub-model"

    def test_judge_returns_result_payload(self, client) -> None:
        c, service = client
        rsp = c.post(
            "/api/vlm-judge/judge",
            json={
                "dataset_id": "synthetic",
                "episode_index": 0,
            },
        )
        assert rsp.status_code == 202
        assert len(service.calls) == 0
        assert asyncio.run(c.app.state.judge_jobs.run_once())
        job = c.get(rsp.headers["Location"]).json()
        assert job["status"] == "succeeded"
        body = job["targets"][0]["result"]
        assert body["episode_id"] == "synthetic/episode_000000"
        assert body["outcome_success"] is True
        assert len(service.calls) == 1
        assert service.calls[0]["instruction"] == "pick orange"

    def test_judge_records_missing_video_as_execution_error(self, client) -> None:
        c, service = client

        def raise_fnf(**kwargs: object) -> JudgeResult:
            raise FileNotFoundError(f"missing: {kwargs['video_paths']}")

        service.judge_episode = raise_fnf  # type: ignore[assignment]
        rsp = c.post(
            "/api/vlm-judge/judge",
            json={
                "dataset_id": "synthetic",
                "episode_index": 0,
            },
        )
        assert rsp.status_code == 202
        assert asyncio.run(c.app.state.judge_jobs.run_once())
        job = c.get(rsp.headers["Location"]).json()
        assert job["status"] == "failed"
        assert job["targets"][0]["error"] == "FileNotFoundError"


@pytest.mark.parametrize("version", ["v2.1", "v3.0"])
@pytest.mark.parametrize(
    "case",
    [
        "outside",
        "absent-outside",
        "traversal",
        "symlink",
        "prefix",
        "symlink-prefix",
        "missing",
        "directory",
        "fifo",
        "suffix",
        "url",
    ],
)
def test_invalid_media_is_rejected_before_service(
    client: tuple[TestClient, StubService],
    tmp_path: Path,
    case: str,
    version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evaluation.vlm_judge.cache import JudgeCache

    connection, service = client
    root = tmp_path / "media"
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"not opened")
    (root / "escape.mp4").symlink_to(outside)
    sibling = tmp_path / "media-other"
    sibling.mkdir()
    (sibling / "episode.mp4").write_bytes(b"not opened")
    (root / "prefix.mp4").symlink_to(sibling / "episode.mp4")
    (root / "directory.mp4").mkdir()
    (root / "notes.txt").write_text("not media")
    os.mkfifo(root / "pipe.mp4")
    paths = {
        "outside": str(outside),
        "absent-outside": str(tmp_path / "absent.mp4"),
        "traversal": "../outside.mp4",
        "symlink": "escape.mp4",
        "prefix": str(sibling / "episode.mp4"),
        "symlink-prefix": "prefix.mp4",
        "missing": "missing.mp4",
        "directory": "directory.mp4",
        "fifo": "pipe.mp4",
        "suffix": "notes.txt",
        "url": "https://example.org/a.mp4",
    }

    _write_dataset(root, paths[case], version=version)
    original_exists = Path.exists

    def guarded_exists(path: Path) -> bool:
        if path == root / paths[case]:
            pytest.fail("Invalid media reached the adapter existence probe")
        return original_exists(path)

    def forbidden_key(*args: object, **kwargs: object) -> None:
        pytest.fail("Invalid media reached cache identity work")

    monkeypatch.setattr(Path, "exists", guarded_exists)
    monkeypatch.setattr(JudgeCache, "key", forbidden_key)
    response = connection.post(
        "/api/vlm-judge/judge",
        json={"dataset_id": "synthetic", "episode_index": 0},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid media input"}
    assert service.calls == []


@pytest.mark.parametrize("version", ["v2.1", "v3.0"])
def test_given_unselected_missing_media_when_judging_then_only_validates_selected_episode(
    client: tuple[TestClient, StubService], tmp_path: Path, version: str
) -> None:
    connection, service = client
    root = tmp_path / "media"
    _write_dataset(root, "ep{episode_index}.mp4", version=version)
    with (root / "meta" / "episodes.jsonl").open("a") as metadata:
        metadata.write(json.dumps({"episode_index": 1, "tasks": ["unselected"], "length": 12}) + "\n")

    response = connection.post("/api/vlm-judge/judge", json={"dataset_id": "synthetic", "episode_index": 0})

    assert response.status_code == 202
    assert asyncio.run(connection.app.state.judge_jobs.run_once())
    assert len(service.calls) == 1
    assert service.calls[0]["video_paths"] == {"front": root / "ep0.mp4"}


@pytest.mark.parametrize(
    "extra",
    [
        {"video_paths": {"front": "/private/input.mp4"}},
        {"instruction": "caller override"},
        {"from_s": 2.0, "to_s": 1.0},
    ],
)
def test_given_legacy_media_fields_when_submitting_ids_then_rejects_and_sanitizes(
    client: tuple[TestClient, StubService], extra: dict[str, object]
) -> None:
    connection, service = client
    response = connection.post("/api/vlm-judge/judge", json={"dataset_id": "synthetic", "episode_index": 0, **extra})

    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}
    assert service.calls == []


@pytest.mark.parametrize("path_kind", ["relative", "absolute", "nested", "symlink"])
def test_contained_media_uses_canonical_path(client, tmp_path: Path, path_kind: str) -> None:
    connection, service = client
    root = tmp_path / "media"
    (root / "alias.mp4").symlink_to(root / "ep0.mp4")
    (root / "nested").mkdir()
    (root / "nested" / "episode.mp4").write_bytes(b"stub video")
    paths = {
        "relative": "ep0.mp4",
        "absolute": str(root / "ep0.mp4"),
        "nested": "nested/episode.mp4",
        "symlink": "alias.mp4",
    }
    expected = root / "nested" / "episode.mp4" if path_kind == "nested" else root / "ep0.mp4"

    _write_dataset(root, paths[path_kind])
    response = connection.post(
        "/api/vlm-judge/judge",
        json={"dataset_id": "synthetic", "episode_index": 0},
    )

    assert response.status_code == 202
    assert asyncio.run(connection.app.state.judge_jobs.run_once())
    assert service.calls[0]["video_paths"] == {"front": expected}


@pytest.mark.parametrize("path_kind", ["relative", "alias-absolute", "canonical-absolute"])
def test_symlinked_media_root_accepts_equivalent_paths(tmp_path: Path, path_kind: str) -> None:
    root = tmp_path / "media"
    root.mkdir()
    video = root / "episode.mp4"
    video.write_bytes(b"stub video")
    alias = tmp_path / "configured-media"
    alias.symlink_to(root, target_is_directory=True)
    paths = {
        "relative": "episode.mp4",
        "alias-absolute": str(alias / "episode.mp4"),
        "canonical-absolute": str(video),
    }
    service = StubService()
    _write_dataset(root, paths[path_kind])
    connection = _connection(alias, service)

    response = connection.post(
        "/judge",
        json={"dataset_id": "synthetic", "episode_index": 0},
    )

    assert response.status_code == 202
    assert asyncio.run(connection.app.state.judge_jobs.run_once())
    assert service.calls[0]["video_paths"] == {"front": video}


@pytest.mark.parametrize("path_kind", ["alias-prefix", "canonical-prefix", "escape", "traversal", "missing"])
def test_symlinked_media_root_rejects_invalid_paths(tmp_path: Path, path_kind: str) -> None:
    root = tmp_path / "media"
    root.mkdir()
    alias = tmp_path / "configured-media"
    alias.symlink_to(root, target_is_directory=True)
    sibling = tmp_path / "media-other"
    sibling.mkdir()
    outside = sibling / "episode.mp4"
    outside.write_bytes(b"not opened")
    sibling_alias = tmp_path / "configured-media-other"
    sibling_alias.symlink_to(sibling, target_is_directory=True)
    (root / "escape.mp4").symlink_to(outside)
    paths = {
        "alias-prefix": str(sibling_alias / "episode.mp4"),
        "canonical-prefix": str(outside),
        "escape": str(alias / "escape.mp4"),
        "traversal": str(alias / ".." / "media-other" / "episode.mp4"),
        "missing": str(alias / "missing.mp4"),
    }
    service = StubService()
    _write_dataset(root, paths[path_kind])
    connection = _connection(alias, service)

    response = connection.post(
        "/judge",
        json={"dataset_id": "synthetic", "episode_index": 0},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid media input"}
    assert service.calls == []


@pytest.mark.parametrize("path_kind", ["outside", "prefix", "symlink-prefix"])
def test_outside_media_is_rejected_before_additional_filesystem_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path_kind: str
) -> None:
    root = tmp_path / "media"
    root.mkdir()
    sibling = tmp_path / "media-other"
    sibling.mkdir()
    outside = sibling / "episode.mp4"
    outside.write_bytes(b"not opened")
    (root / "escape.mp4").symlink_to(outside)
    paths = {"outside": tmp_path / "absent.mp4", "prefix": outside, "symlink-prefix": root / "escape.mp4"}

    def forbidden_probe(*args: object, **kwargs: object) -> None:
        pytest.fail("Rejected media reached a filesystem probe")

    with monkeypatch.context() as patch:
        original_realpath = os.path.realpath

        def resolve_then_forbid_stat(candidate: str | Path, *, strict: bool = False) -> str:
            resolved = original_realpath(candidate, strict=strict)
            patch.setattr(Path, "stat", forbidden_probe)
            return resolved

        patch.setattr(
            os.path, "realpath", resolve_then_forbid_stat if path_kind == "symlink-prefix" else forbidden_probe
        )
        with pytest.raises(MediaInputError, match=r"^Invalid media input$"):
            validate_video_path(paths[path_kind], root)


@pytest.mark.parametrize("root", [None, "/"])
def test_unconfigured_or_filesystem_root_disables_judging(tmp_path: Path, root: str | None) -> None:
    service = StubService()
    app = fastapi.FastAPI()
    app.include_router(
        api_module.build_router(
            service,
            resolver=LocalDatasetResolver({"synthetic": Path(root)} if root else {}),
            job_store=LocalJobStore(tmp_path / "jobs"),
            local_actor="local-test",
            allow_unauthenticated=True,
        )
    )
    connection = TestClient(app)

    response = connection.post("/judge", json={"dataset_id": "synthetic", "episode_index": 0})

    assert response.status_code == 503
    assert service.calls == []
    assert connection.get("/health").status_code == 200


def _write_video(path: Path) -> None:
    import av
    import numpy as np

    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("libx264", rate=12)
        stream.width, stream.height, stream.pix_fmt = 32, 32, "yuv420p"
        for index in range(12):
            frame = av.VideoFrame.from_ndarray(np.full((32, 32, 3), index * 16, dtype=np.uint8), format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


@pytest.mark.parametrize("failure", [None, "corrupt", "backend"])
def test_real_media_errors_and_cache_are_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    from evaluation.vlm_judge.cache import JudgeCache
    from evaluation.vlm_judge.service import BackendConfig, FrameConfig, JudgeService, ServiceConfig

    root = tmp_path / "media"
    root.mkdir()
    video = root / "episode.mp4"
    _write_video(video)
    _write_dataset(root, "episode.mp4")
    service = JudgeService(
        ServiceConfig(backend=BackendConfig(kind="echo"), frames=FrameConfig(n_frames=3), cache_dir=tmp_path / "cache")
    )
    connection = _connection(root, service)
    payload = {"dataset_id": "synthetic", "episode_index": 0}
    if failure == "corrupt":
        video.write_bytes(b"invalid video")

    def forbidden_inference(*args: object, **kwargs: object) -> None:
        if failure == "backend":
            raise ValueError(f"private backend details: {video}")
        pytest.fail("Invalid media reached model inference")

    if failure:
        monkeypatch.setattr(service, "_ensure_agent", forbidden_inference)
    response = connection.post("/judge", json=payload)
    assert response.status_code == 202
    assert asyncio.run(connection.app.state.judge_jobs.run_once())
    job = connection.get(response.headers["Location"]).json()
    if failure:
        assert job["status"] == "failed"
        assert job["targets"][0]["error"] == ("ValueError" if failure == "backend" else "MediaInputError")
        assert "private backend details" not in json.dumps(job)
        assert str(video) not in json.dumps(job)
    else:
        assert job["status"] == "succeeded"
        monkeypatch.setattr(service, "_extract", forbidden_inference)
        cached = connection.post("/judge", json=payload)
        assert cached.status_code == 202
        asyncio.run(connection.app.state.judge_jobs.run_once())
        cached_job = connection.get(cached.headers["Location"]).json()
        assert cached_job["targets"][0]["result"] == job["targets"][0]["result"]
        outside = tmp_path / "outside.mp4"
        video.rename(outside)
        video.symlink_to(outside)
        monkeypatch.setattr(JudgeCache, "key", forbidden_inference)
        rejected = connection.post("/judge", json=payload)
        assert rejected.status_code == 400
        assert rejected.json() == {"detail": "Invalid media input"}


@pytest.mark.parametrize("mounted", [False, True], ids=["standalone", "mounted"])
@pytest.mark.parametrize("body", ["valid", "malformed", "schema"])
@pytest.mark.parametrize(
    "denial",
    [
        "disabled",
        "no-datasets",
        "no-actor",
        "no-store",
        "peer",
        "unknown-peer",
        "host",
        "duplicate-host",
        "malformed-host",
        "origin",
        "null-origin",
        "cross-site",
        "forwarded",
        "not-json",
    ],
)
def test_admission_precedes_body_and_media_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mounted: bool,
    body: str,
    denial: str,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("VLM_JUDGE_ALLOW_UNAUTHENTICATED", "true")
    monkeypatch.setenv("VLM_JUDGE_DATASETS", json.dumps({"synthetic": str(tmp_path)}))
    monkeypatch.setenv("VLM_JUDGE_LOCAL_ACTOR", "local-test")
    monkeypatch.setenv("VLM_JUDGE_JOB_DIR", str(tmp_path / "jobs"))
    if denial == "disabled":
        monkeypatch.delenv("VLM_JUDGE_ALLOW_UNAUTHENTICATED")
    elif denial == "no-datasets":
        monkeypatch.delenv("VLM_JUDGE_DATASETS")
    elif denial == "no-actor":
        monkeypatch.delenv("VLM_JUDGE_LOCAL_ACTOR")
    elif denial == "no-store":
        monkeypatch.delenv("VLM_JUDGE_JOB_DIR")
    service = StubService()
    if mounted:
        app = FastAPI()
        app.include_router(
            api_module.build_router(
                service,
                resolver=LocalDatasetResolver({} if denial == "no-datasets" else {"synthetic": tmp_path}),
                local_actor=None if denial == "no-actor" else "local-test",
                job_store=None if denial == "no-store" else LocalJobStore(tmp_path / "jobs"),
            ),
            prefix="/mounted",
        )
        path = "/mounted/judge"
    else:
        monkeypatch.setattr("evaluation.vlm_judge.service.JudgeService", lambda config: service)
        app = api_module.build_app()
        path = "/judge"
    peer = ("192.0.2.1", 50000) if denial in {"peer", "disabled", "no-datasets"} else ("127.0.0.1", 50000)
    if denial == "unknown-peer":
        peer = None
    headers = {"content-type": "application/json"}
    overrides = {
        "host": {"host": "attacker.example"},
        "malformed-host": {"host": "localhost:invalid"},
        "origin": {"origin": "https://attacker.example"},
        "null-origin": {"origin": "null"},
        "cross-site": {"sec-fetch-site": "cross-site"},
        "forwarded": {"x-forwarded-for": "127.0.0.1"},
        "not-json": {"content-type": "text/plain"},
    }
    headers.update(overrides.get(denial, {}))
    wire_headers = list(headers.items())
    if denial == "duplicate-host":
        wire_headers.extend([("host", "localhost"), ("host", "attacker.example")])
    payload = {"dataset_id": "synthetic", "episode_index": 0}
    content = (
        json.dumps(payload)
        if body == "valid"
        else '{"private":'
        if body == "malformed"
        else '{"video_paths": "/private/input.mp4"}'
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Denied request reached root/path/service work")

    monkeypatch.setattr(LocalDatasetResolver, "resolve", forbidden)
    monkeypatch.setattr(LocalDatasetResolver, "episode_indices", forbidden)
    monkeypatch.setattr(service, "judge_episode", forbidden)
    connection = TestClient(app, base_url="http://localhost", client=peer)
    response = connection.post(path, content=content, headers=wire_headers)

    expected = 503 if denial in {"disabled", "no-datasets", "no-actor", "no-store"} else 403
    assert response.status_code == expected
    assert response.json() == {"detail": "Judge unavailable" if expected == 503 else "Request forbidden"}


@pytest.mark.parametrize("body", ['{"private":', '{"video_paths": "/private/input.mp4"}'])
def test_admitted_validation_errors_are_sanitized(client, body: str) -> None:
    connection, service = client
    response = connection.post("/api/vlm-judge/judge", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}
    assert service.calls == []


@pytest.mark.parametrize(
    "host,peer", [("localhost:8080", "127.0.0.1"), ("127.0.0.1", "127.0.0.1"), ("[::1]:8080", "::1")]
)
def test_loopback_peer_host_and_matching_origin_are_admitted(
    tmp_path: Path,
    host: str,
    peer: str,
) -> None:
    (tmp_path / "video.mp4").write_bytes(b"stub")
    _write_dataset(tmp_path, "video.mp4")
    service = StubService()
    app = _connection(tmp_path, service).app
    connection = TestClient(app, base_url="http://localhost", client=(peer, 50000))
    response = connection.post(
        "/judge",
        headers={"host": host, "origin": f"http://{host}"},
        json={
            "dataset_id": "synthetic",
            "episode_index": 0,
        },
    )
    assert response.status_code == 202
    assert asyncio.run(app.state.judge_jobs.run_once())
    assert len(service.calls) == 1


@pytest.mark.parametrize("host", [None, "127.0.0.1", "::1", "localhost", "0.0.0.0", "::", "192.0.2.1", "example.org"])
def test_launcher_preview_is_loopback_only(host: str | None) -> None:
    root = Path(__file__).resolve().parents[5]
    command = [
        "bash",
        str(root / "data-management/curation/scripts/serve-api.sh"),
        "--config-preview",
        "--backend",
        "echo",
    ]
    if host:
        command.extend(["--host", host])
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=10, check=False)
    if host in {None, "127.0.0.1", "::1", "localhost"}:
        assert result.returncode == 0
        assert "--no-proxy-headers" in result.stdout
        assert f"--host {host or '127.0.0.1'}" in result.stdout
    else:
        assert result.returncode != 0
        assert "loopback" in result.stderr.lower() + result.stdout.lower()


@pytest.mark.parametrize(("method", "path"), _LIFECYCLE_ENDPOINTS)
def test_global_app_is_default_deny(monkeypatch: pytest.MonkeyPatch, method: str, path: str) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.delenv("VLM_JUDGE_ALLOW_UNAUTHENTICATED", raising=False)
    monkeypatch.delenv("VLM_JUDGE_DATASETS", raising=False)
    app = importlib.reload(api_module).app
    connection = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))
    assert connection.get("/health").status_code == 200
    assert connection.request(method, path, content="malformed").status_code == 503


class TestAccessibilityDocumentationApps:
    def test_vlm_echo_app_exposes_documentation_without_model_loading(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange
        monkeypatch.setenv("VLM_JUDGE_BACKEND", "echo")
        monkeypatch.setenv("VLM_JUDGE_CACHE_DIR", "")

        # Act
        with TestClient(api_module.build_app()) as documentation_client:
            responses = [documentation_client.get(path) for path in ("/docs", "/redoc", "/openapi.json", "/health")]

        # Assert
        assert all(response.status_code == fastapi.status.HTTP_200_OK for response in responses)
        assert responses[-1].json()["backend_kind"] == "echo"


def _curation_arguments(root: Path) -> list[str]:
    return [
        "--dataset",
        str(root),
        "--output",
        str(root / "results.jsonl"),
        "--backend",
        "echo",
        "--cache-dir",
        "",
        "--n-frames",
        "3",
    ]


def test_given_config_preview_when_running_then_omits_endpoint_and_credentials(
    jsonl_dataset: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = curation_run.main(
        [
            *_curation_arguments(jsonl_dataset),
            "--config-preview",
            "--api-key",
            "synthetic-token",
            "--base-url",
            "https://example.invalid/v1",
        ]
    )

    configuration = json.loads(capsys.readouterr().out)
    assert result == 0
    assert configuration["backend"] == "echo"
    assert {"api_key", "base_url", "config_preview"}.isdisjoint(configuration)
    assert not (jsonl_dataset / "results.jsonl").exists()


def test_given_empty_episode_selection_when_dry_running_then_returns_failure(jsonl_dataset: Path) -> None:
    assert curation_run.main([*_curation_arguments(jsonl_dataset), "--dry-run", "--indices", "99"]) == 1


def test_given_real_episodes_when_dry_running_then_writes_inputs_and_config_without_inference(
    jsonl_dataset: Path,
) -> None:
    result = curation_run.main(
        [
            *_curation_arguments(jsonl_dataset),
            "--dry-run",
            "--indices",
            "0-1, 1, ",
            "--instruction",
            "Inspect the block",
        ]
    )

    rows = [json.loads(line) for line in (jsonl_dataset / "results.jsonl").read_text().splitlines()]
    configuration = json.loads((jsonl_dataset / "results.jsonl.config.json").read_text())
    assert result == 0
    assert len(rows) == 2
    assert all(row["dry_run"] and row["instruction"] == "Inspect the block" for row in rows)
    assert configuration["instruction_origin"] == "cli-override"
    assert len(configuration["saved_inputs"]) == 2


def test_given_unknown_dataset_when_running_status_then_reports_safe_command_failure(
    jsonl_dataset: Path, caplog: pytest.LogCaptureFixture
) -> None:
    result = curation_run.main([*_curation_arguments(jsonl_dataset), "--operation", "status"])

    assert result == 2
    assert "category=ValueError" in caplog.text


@pytest.mark.parametrize("instruction", [None, "Inspect", ""])
def test_given_resolved_episode_when_processing_then_validates_instruction_and_records_echo_result(
    jsonl_dataset: Path, instruction: str | None
) -> None:
    episode = next(dataset_module.iter_episodes(jsonl_dataset))
    if instruction == "":
        episode = replace(episode, instruction="")
    service = JudgeService(ServiceConfig(backend=BackendConfig(kind="echo"), frames=FrameConfig(n_frames=3)))

    if instruction == "":
        with pytest.raises(ValueError, match="Invalid judge result field: instruction"):
            curation_run._process_episode(
                episode=episode,
                service=service,
                instruction_override=instruction,
                dry_run=False,
                force=True,
            )
        return

    result = curation_run._process_episode(
        episode=episode,
        service=service,
        instruction_override=instruction,
        dry_run=False,
        force=True,
    )

    assert result["instruction"] == (instruction or episode.instruction)
    assert result["duration_s"] == 1
    assert result["outcome_success"] is True
    assert result["video_paths"] == {"front": str(episode.video_paths["front"])}
