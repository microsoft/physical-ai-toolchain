"""Behavioral tests for the FastAPI router."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from vlm_judge.api import build_router
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
def client():
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("httpx")  # required by TestClient
    from fastapi.testclient import TestClient

    service = StubService()
    app = fastapi.FastAPI()
    app.include_router(build_router(service), prefix="/api/vlm-judge")
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
                "episode_id": "ep0",
                "instruction": "pick orange",
                "video_paths": {"front": "/tmp/ep0.mp4"},
            },
        )
        assert rsp.status_code == 200
        body = rsp.json()
        assert body["episode_id"] == "ep0"
        assert body["outcome_success"] is True
        assert len(service.calls) == 1
        assert service.calls[0]["instruction"] == "pick orange"

    def test_judge_404_on_missing_video(self, client) -> None:
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
        assert rsp.status_code == 404
