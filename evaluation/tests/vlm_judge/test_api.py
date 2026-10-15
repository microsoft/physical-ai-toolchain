"""Behavioral tests for the FastAPI router."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import fastapi
import pytest
import vlm_judge.api as api_module
from fastapi.testclient import TestClient
from PIL import Image
from pytest_mock import MockerFixture
from vlm_judge.frames import MediaInputError, validate_video_path
from vlm_judge.job_storage import LocalJobStore
from vlm_judge.judge import JudgeResult
from vlm_judge.prompts import PROMPT_VERSION
from vlm_judge.saved_input import LocalDatasetResolver
from vlm_judge.service import JudgeService


class StubService(JudgeService):
    """Lightweight stand-in for ``JudgeService`` that records calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

        from vlm_judge.service import (
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


def _connection(root: Path, service: JudgeService, *, prefix: str = "") -> TestClient:
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
    from vlm_judge.cache import JudgeCache

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
    from vlm_judge.cache import JudgeCache
    from vlm_judge.service import BackendConfig, FrameConfig, JudgeService, ServiceConfig

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
        monkeypatch.setattr(api_module, "JudgeService", lambda config: service)
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
    root = Path(__file__).resolve().parents[3]
    command = ["bash", str(root / "evaluation/vlm_judge/scripts/serve-api.sh"), "--config-preview", "--backend", "echo"]
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
    import importlib

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

    def test_openai_echo_app_exposes_safe_documentation_and_completion(self) -> None:
        # Arrange
        from vlm_judge.openai_shim import build_echo_app

        # Act
        with TestClient(build_echo_app()) as documentation_client:
            docs_response = documentation_client.get("/docs")
            redoc_response = documentation_client.get("/redoc")
            completion_response = documentation_client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "Return a deterministic test response"}]},
            )

        # Assert
        assert docs_response.status_code == 200
        assert redoc_response.status_code == 200
        assert completion_response.status_code == 200
        assert completion_response.json()["model"] == "echo"


def test_given_echo_shim_when_querying_health_and_models_then_lists_the_served_model() -> None:
    from vlm_judge.openai_shim import build_echo_app

    with TestClient(build_echo_app()) as client:
        health = client.get("/health")
        models = client.get("/v1/models")

    assert health.json() == {"status": "ok", "model": "echo"}
    assert models.json()["object"] == "list"
    assert models.json()["data"][0]["id"] == "echo"


@pytest.mark.parametrize(
    ("body", "status", "detail"),
    [
        ({"messages": [], "stream": True}, 400, "Streaming is not supported"),
        ({"messages": [], "max_tokens": 0}, 422, ""),
        (
            {
                "messages": [
                    {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "file:///example.png"}}]}
                ]
            },
            400,
            "Unsupported image_url scheme",
        ),
        (
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "image_url", "image_url": {"url": "https://example.invalid/image.png"}}],
                    }
                ]
            },
            400,
            "Remote image URLs are disabled",
        ),
    ],
    ids=["streaming", "invalid-token-limit", "file-url", "remote-url"],
)
def test_given_unsupported_shim_request_when_completing_then_rejects_request(
    body: dict[str, object], status: int, detail: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vlm_judge import openai_shim

    monkeypatch.setattr(openai_shim, "_ALLOW_REMOTE_IMAGES", False)
    with TestClient(openai_shim.build_echo_app()) as client:
        response = client.post("/v1/chat/completions", json=body)

    assert response.status_code == status
    if detail:
        assert detail in response.json()["detail"]


def test_given_multimodal_history_when_completing_then_decodes_frames_and_returns_echo_progress() -> None:
    from vlm_judge.openai_shim import build_echo_app

    buffer = io.BytesIO()
    Image.new("RGBA", (10, 12), "red").save(buffer, "PNG")
    image_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    body = {
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
        ]
    }
    with TestClient(build_echo_app()) as client:
        response = client.post("/v1/chat/completions", json=body)

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "[0, 100]"


def test_given_shim_launch_arguments_when_starting_then_forwards_model_and_server_configuration(
    monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    from vlm_judge import openai_shim

    uvicorn = ModuleType("uvicorn")
    serve = mocker.Mock()
    uvicorn.run = serve
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)
    model = mocker.patch.object(openai_shim, "Qwen3VLBackend", return_value=openai_shim.EchoBackend())

    result = openai_shim.main(
        [
            "--host",
            "127.0.0.1",
            "--port",
            "8123",
            "--model-id",
            "synthetic-model",
            "--model-revision",
            "synthetic-revision",
            "--device-map",
            "cpu",
            "--dtype",
            "float32",
        ]
    )

    assert result == 0
    model.assert_called_once_with(
        model_id="synthetic-model", revision="synthetic-revision", device_map="cpu", dtype="float32"
    )
    assert serve.call_args.kwargs == {"host": "127.0.0.1", "port": 8123, "log_level": "info"}
    with TestClient(serve.call_args.args[0]) as client:
        assert client.get("/health").json()["model"] == "echo"
