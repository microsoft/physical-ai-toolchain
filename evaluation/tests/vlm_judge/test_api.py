"""Behavioral tests for the FastAPI router."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic")

from vlm_judge.api import build_router
from vlm_judge.judge import JudgeResult
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
def client(tmp_path: Path):
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("httpx")  # required by TestClient
    from fastapi.testclient import TestClient
    from vlm_judge.dataset import EpisodeRecord
    from vlm_judge.job_storage import LocalJobStore
    from vlm_judge.saved_input import SavedInputSnapshot

    service = StubService()

    class Resolver:
        async def resolve(self, dataset_id: str, episode_index: int, **kwargs: object) -> tuple[object, object]:
            return EpisodeRecord(
                f"{dataset_id}/episode_{episode_index:06d}",
                episode_index,
                "pick orange",
                30,
                30,
                {"front": Path("/synthetic/ep0.mp4")},
                None,
                None,
                media_identity={"front": "synthetic-v1"},
            ), SavedInputSnapshot(
                snapshot_id="saved-revision",
                dataset_id=dataset_id,
                episode_index=episode_index,
                principal_scope_id=str(kwargs["principal_scope_id"]),
                source_id="source",
                source_revision="version",
                annotation_author_id=None,
                annotation_revision=None,
                edit_revision=None,
                instruction="pick orange",
                instruction_origin="dataset",
            )

    app = fastapi.FastAPI()
    app.include_router(
        build_router(
            service, resolver=Resolver(), local_actor="local-test", job_store=LocalJobStore(tmp_path / "jobs")
        ),
        prefix="/api/vlm-judge",
    )
    return TestClient(app), service


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


class TestAccessibilityDocumentationApps:
    def test_vlm_echo_app_exposes_documentation_without_model_loading(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange
        fastapi = pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient
        from vlm_judge.api import build_app

        monkeypatch.setenv("VLM_JUDGE_BACKEND", "echo")
        monkeypatch.setenv("VLM_JUDGE_CACHE_DIR", "")

        # Act
        with TestClient(build_app()) as documentation_client:
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
