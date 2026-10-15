"""Behavioral tests for the FastAPI router."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic")

import vlm_judge.api as api_module
from vlm_judge.frames import MediaInputError, validate_video_path
from vlm_judge.judge import JudgeResult


class StubService:
    """Lightweight stand-in for ``JudgeService`` that records calls."""

    def __init__(self) -> None:
        self.model_id = "stub-model"
        self.calls: list[dict[str, Any]] = []

        from vlm_judge.service import (
            BackendConfig,
            FrameConfig,
            ServiceConfig,
        )

        self.config = ServiceConfig(
            backend=BackendConfig(kind="echo"),
            frames=FrameConfig(),
            cache_dir=None,
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
            judge_model=self.model_id,
            prompt_version="test-v1",
            n_frames=8,
            outcome_success=True,
            outcome_confidence=0.9,
            outcome_n_valid_votes=3,
            progress_per_frame=[0, 14, 28, 42, 57, 71, 85, 100],
            voc=0.95,
            milestones=[],
            failure_mode=None,
        )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("httpx")  # required by TestClient
    from fastapi.testclient import TestClient

    service = StubService()
    root = tmp_path / "media"
    root.mkdir()
    (root / "ep0.mp4").write_bytes(b"stub video")
    monkeypatch.setenv("VLM_JUDGE_MEDIA_ROOT", str(root))
    monkeypatch.setenv("VLM_JUDGE_ALLOW_UNAUTHENTICATED", "true")
    app = fastapi.FastAPI()
    app.include_router(api_module.build_router(service), prefix="/api/vlm-judge")
    return TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)), service


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
                "episode_id": "ep0",
                "instruction": "pick orange",
                "video_paths": {"front": "ep0.mp4"},
            },
        )
        assert rsp.status_code == 200
        body = rsp.json()
        assert body["episode_id"] == "ep0"
        assert body["outcome_success"] is True
        assert len(service.calls) == 1
        assert service.calls[0]["instruction"] == "pick orange"

    def test_judge_generic_400_on_missing_video(self, client) -> None:
        c, service = client

        def raise_fnf(**kwargs: object) -> JudgeResult:
            raise FileNotFoundError(f"missing: {kwargs['video_paths']}")

        service.judge_episode = raise_fnf  # type: ignore[assignment]
        rsp = c.post(
            "/api/vlm-judge/judge",
            json={
                "episode_id": "ep0",
                "instruction": "x",
                "video_paths": {"front": "/no/such/file.mp4"},
            },
        )
        assert rsp.status_code == 400
        assert rsp.json() == {"detail": "Invalid media input"}


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
def test_invalid_media_is_rejected_before_service(client, tmp_path: Path, case: str) -> None:
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

    response = connection.post(
        "/api/vlm-judge/judge",
        json={
            "episode_id": "ep0",
            "instruction": "pick cube",
            "video_paths": {"first": "ep0.mp4", "second": paths[case]},
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid media input"}
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

    response = connection.post(
        "/api/vlm-judge/judge",
        json={
            "episode_id": "ep0",
            "instruction": "pick cube",
            "video_paths": {"front": paths[path_kind]},
        },
    )

    assert response.status_code == 200
    assert service.calls[0]["video_paths"] == {"front": expected}


@pytest.mark.parametrize("path_kind", ["relative", "alias-absolute", "canonical-absolute"])
def test_symlinked_media_root_accepts_equivalent_paths(tmp_path: Path, path_kind: str) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

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
    app = FastAPI()
    app.include_router(api_module.build_router(service, media_root=alias, allow_unauthenticated=True))
    connection = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))

    response = connection.post(
        "/judge",
        json={"episode_id": "ep0", "instruction": "pick cube", "video_paths": {"front": paths[path_kind]}},
    )

    assert response.status_code == 200
    assert service.calls[0]["video_paths"] == {"front": video}


@pytest.mark.parametrize("path_kind", ["alias-prefix", "canonical-prefix", "escape", "traversal", "missing"])
def test_symlinked_media_root_rejects_invalid_paths(tmp_path: Path, path_kind: str) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

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
    app = FastAPI()
    app.include_router(api_module.build_router(service, media_root=alias, allow_unauthenticated=True))
    connection = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))

    response = connection.post(
        "/judge",
        json={"episode_id": "ep0", "instruction": "pick cube", "video_paths": {"front": paths[path_kind]}},
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
def test_unconfigured_or_filesystem_root_disables_judging(monkeypatch: pytest.MonkeyPatch, root: str | None) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.delenv("VLM_JUDGE_MEDIA_ROOT", raising=False)
    if root:
        monkeypatch.setenv("VLM_JUDGE_MEDIA_ROOT", root)
    service = StubService()
    app = FastAPI()
    app.include_router(api_module.build_router(service))
    connection = TestClient(app)

    response = connection.post(
        "/judge", json={"episode_id": "ep0", "instruction": "x", "video_paths": {"front": "anything.mp4"}}
    )

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


@pytest.mark.parametrize("failure", [None, "corrupt", "window", "backend"])
def test_real_media_errors_and_cache_are_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from vlm_judge.cache import JudgeCache
    from vlm_judge.service import BackendConfig, FrameConfig, JudgeService, ServiceConfig

    root = tmp_path / "media"
    root.mkdir()
    video = root / "episode.mp4"
    _write_video(video)
    service = JudgeService(
        ServiceConfig(backend=BackendConfig(kind="echo"), frames=FrameConfig(n_frames=3), cache_dir=tmp_path / "cache")
    )
    app = FastAPI()
    monkeypatch.setenv("VLM_JUDGE_ALLOW_UNAUTHENTICATED", "true")
    app.include_router(api_module.build_router(service, media_root=root))
    connection = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))
    payload = {"episode_id": "ep0", "instruction": "pick cube", "video_paths": {"front": "episode.mp4"}}
    if failure == "corrupt":
        video.write_bytes(b"invalid video")
    elif failure == "window":
        payload.update(from_s=2.0, to_s=1.0)

    def forbidden_inference(*args: object, **kwargs: object) -> None:
        if failure == "backend":
            raise ValueError(f"private backend details: {video}")
        pytest.fail("Invalid media reached model inference")

    if failure:
        monkeypatch.setattr(service, "_ensure_agent", forbidden_inference)
    response = connection.post("/judge", json=payload)
    if failure:
        assert response.status_code == (502 if failure == "backend" else 400)
        assert response.json() == {"detail": "VLM backend error" if failure == "backend" else "Invalid media input"}
    else:
        assert response.status_code == 200
        monkeypatch.setattr(service, "_extract", forbidden_inference)
        assert connection.post("/judge", json=payload).json() == response.json()
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
        "no-root",
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
    monkeypatch.setenv("VLM_JUDGE_MEDIA_ROOT", str(tmp_path))
    if denial == "disabled":
        monkeypatch.delenv("VLM_JUDGE_ALLOW_UNAUTHENTICATED")
    elif denial == "no-root":
        monkeypatch.delenv("VLM_JUDGE_MEDIA_ROOT")
    service = StubService()
    if mounted:
        app = FastAPI()
        app.include_router(api_module.build_router(service), prefix="/mounted")
        path = "/mounted/judge"
    else:
        monkeypatch.setattr(api_module, "JudgeService", lambda config: service)
        app = api_module.build_app()
        path = "/judge"
    peer = ("192.0.2.1", 50000) if denial in {"peer", "disabled", "no-root"} else ("127.0.0.1", 50000)
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
    payload = {"episode_id": "ep", "instruction": "x", "video_paths": {"front": "/private/input.mp4"}}
    content = (
        json.dumps(payload)
        if body == "valid"
        else '{"private":'
        if body == "malformed"
        else '{"video_paths": "/private/input.mp4"}'
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Denied request reached root/path/service work")

    monkeypatch.setattr(api_module, "validate_media_root", forbidden)
    monkeypatch.setattr(api_module, "validate_video_path", forbidden)
    monkeypatch.setattr(service, "judge_episode", forbidden)
    connection = TestClient(app, base_url="http://localhost", client=peer)
    response = connection.post(path, content=content, headers=wire_headers)

    expected = 503 if denial in {"disabled", "no-root"} else 403
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
    monkeypatch: pytest.MonkeyPatch,
    host: str,
    peer: str,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("VLM_JUDGE_ALLOW_UNAUTHENTICATED", "true")
    (tmp_path / "video.mp4").write_bytes(b"stub")
    service = StubService()
    app = FastAPI()
    app.include_router(api_module.build_router(service, media_root=tmp_path))
    connection = TestClient(app, base_url="http://localhost", client=(peer, 50000))
    response = connection.post(
        "/judge",
        headers={"host": host, "origin": f"http://{host}"},
        json={
            "episode_id": "ep",
            "instruction": "x",
            "video_paths": {"front": "video.mp4"},
        },
    )
    assert response.status_code == 200
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


def test_global_app_is_default_deny(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    from fastapi.testclient import TestClient

    monkeypatch.delenv("VLM_JUDGE_ALLOW_UNAUTHENTICATED", raising=False)
    monkeypatch.delenv("VLM_JUDGE_MEDIA_ROOT", raising=False)
    app = importlib.reload(api_module).app
    connection = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))
    assert connection.get("/health").status_code == 200
    assert connection.post("/judge", content="malformed").status_code == 503


class TestAccessibilityDocumentationApps:
    def test_vlm_echo_app_exposes_documentation_without_model_loading(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange
        fastapi = pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient

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
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient
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
