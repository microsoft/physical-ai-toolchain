"""HTTP API for the VLM-as-judge service.

Exposes a small FastAPI ``APIRouter`` that the dataviewer backend can mount
under ``/api/vlm-judge`` and the policy-evaluation pipeline can consume from
a sidecar process. The router is intentionally framework-thin — all real
work happens in :class:`evaluation.vlm_judge.service.JudgeService`.

Run as a standalone server:

    uvicorn evaluation.vlm_judge.api:app --host 0.0.0.0 --port 8080

Mount inside an existing FastAPI app:

    from evaluation.vlm_judge.api import build_router
    from evaluation.vlm_judge.service import JudgeService, ServiceConfig

    judge_service = JudgeService(ServiceConfig(...))
    app.include_router(build_router(judge_service), prefix="/api/vlm-judge")
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .service import (
    BackendConfig,
    FrameConfig,
    JudgeService,
    ServiceConfig,
)

_LOGGER = logging.getLogger("evaluation.vlm_judge")


class JudgeRequest(BaseModel):
    """Body for ``POST /judge``."""

    episode_id: str = Field(..., description="Stable identifier echoed in the response")
    instruction: str = Field(..., description="Natural-language task instruction")
    video_paths: dict[str, str] = Field(
        ...,
        description="Map of view name -> MP4 path on a filesystem accessible to the service",
    )
    from_s: float | None = Field(default=None, description="Optional window start in seconds")
    to_s: float | None = Field(default=None, description="Optional window end in seconds")
    force: bool = Field(default=False, description="Bypass the cache")


class JudgeResponse(BaseModel):
    """Response for ``POST /judge``."""

    episode_id: str
    instruction: str
    judge_model: str
    prompt_version: str
    n_frames: int
    outcome_success: bool | None
    outcome_confidence: float
    outcome_n_valid_votes: int
    progress_per_frame: list[int]
    voc: float
    milestones: list[dict[str, Any]] = []
    failure_mode: str | None = None


def build_router(service: JudgeService):
    """Return a FastAPI router bound to ``service``."""
    from fastapi import APIRouter, HTTPException

    router = APIRouter(tags=["vlm-judge"])

    @router.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "model_id": service.model_id,
            "backend_kind": service.config.backend.kind,
            "cache_enabled": service.config.cache_dir is not None,
        }

    @router.post("/judge", response_model=JudgeResponse)
    def judge(request: JudgeRequest) -> JudgeResponse:
        try:
            result = service.judge_episode(
                episode_id=request.episode_id,
                instruction=request.instruction,
                video_paths={k: Path(v) for k, v in request.video_paths.items()},
                from_s=request.from_s,
                to_s=request.to_s,
                force=request.force,
            )
        except FileNotFoundError as err:
            raise HTTPException(status_code=404, detail=str(err)) from err
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        return JudgeResponse(**result.to_dict())

    return router


def build_app():
    """Construct a standalone FastAPI app from environment variables.

    Environment variables:

    - ``VLM_JUDGE_BACKEND``  (default ``echo`` for safe boot without GPU)
    - ``VLM_JUDGE_MODEL_ID`` (default ``Qwen/Qwen3-VL-4B-Instruct``)
    - ``VLM_JUDGE_MODEL_REVISION`` (immutable HF commit SHA to pin the qwen3-vl download)
    - ``VLM_JUDGE_BASE_URL`` (required for ``openai-compat``)
    - ``VLM_JUDGE_API_KEY``  (optional API key for ``openai-compat``)
    - ``VLM_JUDGE_N_FRAMES`` (default ``12``)
    - ``VLM_JUDGE_CACHE_DIR`` (default ``outputs/vlm-judge/cache``)
    """
    from fastapi import FastAPI

    backend = BackendConfig(
        kind=os.environ.get("VLM_JUDGE_BACKEND", "echo"),
        model_id=os.environ.get("VLM_JUDGE_MODEL_ID", "Qwen/Qwen3-VL-4B-Instruct"),
        revision=os.environ.get("VLM_JUDGE_MODEL_REVISION") or None,
        base_url=os.environ.get("VLM_JUDGE_BASE_URL") or None,
        api_key=os.environ.get("VLM_JUDGE_API_KEY") or None,
    )
    frames = FrameConfig(
        n_frames=int(os.environ.get("VLM_JUDGE_N_FRAMES", "12")),
    )
    cache_dir_env = os.environ.get("VLM_JUDGE_CACHE_DIR", "outputs/vlm-judge/cache")
    cache_dir = Path(cache_dir_env) if cache_dir_env else None

    service = JudgeService(ServiceConfig(backend=backend, frames=frames, cache_dir=cache_dir))
    app = FastAPI(title="VLM-as-Judge", version="0.1.0")
    app.include_router(build_router(service))
    _LOGGER.info("VLM judge API ready (backend=%s, model=%s)", backend.kind, backend.model_id)
    return app


app = build_app()
