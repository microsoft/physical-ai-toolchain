"""HTTP API for the VLM-as-judge service.

Exposes a local-only FastAPI router. The viewer uses a separate authenticated
dataset router backed by the same JudgeService. Standalone judging requires
explicit local opt-in and a confined media root; forwarding is unsupported.

Run as a standalone server:

    uvicorn evaluation.vlm_judge.api:app --host 127.0.0.1 --port 8080 --no-proxy-headers

Mount inside an existing FastAPI app:

    from evaluation.vlm_judge.api import build_router
    from evaluation.vlm_judge.service import JudgeService, ServiceConfig

    judge_service = JudgeService(ServiceConfig(...))
    app.include_router(build_router(judge_service), prefix="/api/vlm-judge")
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Coroutine
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import Response

from .frames import MediaInputError, validate_media_root, validate_video_path
from .service import (
    BackendConfig,
    FrameConfig,
    JudgeService,
    ServiceConfig,
)
from .swagger_ui import install_responsive_swagger_ui

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


def _local_authority(authority: str, scheme: str) -> tuple[str, str, int]:
    """Parse a literal loopback authority without DNS or forwarded headers."""
    if (
        scheme not in {"http", "https"}
        or not authority
        or any(character.isspace() or character in "/\\?#@%," for character in authority)
    ):
        raise ValueError("Invalid authority")
    parsed = urlsplit(f"//{authority}")
    host = parsed.hostname
    if not host or authority.endswith(":"):
        raise ValueError("Invalid authority")
    if host != "localhost":
        address = ip_address(host)
        if not address.is_loopback:
            raise ValueError("Non-loopback authority")
        host = str(address)
    port = parsed.port
    if port == 0:
        raise ValueError("Invalid port")
    return scheme, host, port if port is not None else (443 if scheme == "https" else 80)


def _admit_local_request(request: Request) -> bool:
    """Accept only unproxied JSON requests from a local peer and browser origin."""
    try:
        if request.client is None or not ip_address(request.client.host).is_loopback:
            return False
        if any(name == "forwarded" or name.startswith("x-forwarded-") for name in request.headers):
            return False
        hosts = request.headers.getlist("host")
        if len(hosts) != 1:
            return False
        authority = _local_authority(hosts[0], request.scope.get("scheme", "http"))
        origins = request.headers.getlist("origin")
        if len(origins) > 1:
            return False
        if origins:
            origin = urlsplit(origins[0])
            if origin.path or origin.query or origin.fragment:
                return False
            if _local_authority(origin.netloc, origin.scheme) != authority:
                return False
        if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
            return False
        content_types = request.headers.getlist("content-type")
        return len(content_types) == 1 and content_types[0].split(";", 1)[0].strip().lower() == "application/json"
    except ValueError:
        return False


def build_router(
    service: JudgeService,
    *,
    media_root: Path | str | None = None,
    allow_unauthenticated: bool | None = None,
):
    """Return a FastAPI router bound to ``service``."""
    from fastapi import APIRouter, HTTPException
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from fastapi.routing import APIRoute

    configured_root = media_root if media_root is not None else os.environ.get("VLM_JUDGE_MEDIA_ROOT")
    enabled = (
        allow_unauthenticated
        if allow_unauthenticated is not None
        else os.environ.get("VLM_JUDGE_ALLOW_UNAUTHENTICATED", "").lower() == "true"
    )

    class LocalJudgeRoute(APIRoute):
        def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
            handler = super().get_route_handler()
            if self.name != "judge":
                return handler

            async def admitted_handler(request: Request) -> Response:
                if (
                    not enabled
                    or configured_root is None
                    or not str(configured_root).strip()
                    or str(configured_root) == Path(configured_root).anchor
                ):
                    raise HTTPException(status_code=503, detail="Judge unavailable")
                if not _admit_local_request(request):
                    raise HTTPException(status_code=403, detail="Request forbidden")
                try:
                    return await handler(request)
                except RequestValidationError:
                    return JSONResponse(status_code=422, content={"detail": "Invalid request"})

            return admitted_handler

    router = APIRouter(tags=["vlm-judge"], route_class=LocalJudgeRoute)

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
            root = validate_media_root(configured_root)
        except (OSError, RuntimeError, ValueError) as err:
            raise HTTPException(status_code=503, detail="Judge unavailable") from err
        try:
            if not request.video_paths:
                raise MediaInputError("Invalid media input")
            video_paths = {
                view: validate_video_path(path, root, root_alias=configured_root)
                for view, path in request.video_paths.items()
            }
            result = service.judge_episode(
                episode_id=request.episode_id,
                instruction=request.instruction,
                video_paths=video_paths,
                from_s=request.from_s,
                to_s=request.to_s,
                force=request.force,
            )
        except MediaInputError as err:
            _LOGGER.exception("Invalid judge media")
            raise HTTPException(status_code=400, detail="Invalid media input") from err
        except Exception as err:
            _LOGGER.exception("VLM backend failed")
            raise HTTPException(status_code=502, detail="VLM backend error") from err
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
    - ``VLM_JUDGE_ALLOW_UNAUTHENTICATED`` (must be ``true`` for local judging)
    - ``VLM_JUDGE_MEDIA_ROOT`` (explicit narrow directory; no default)
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
    app = FastAPI(title="VLM-as-Judge", version="0.1.0", docs_url=None)
    install_responsive_swagger_ui(app)
    app.include_router(build_router(service))
    _LOGGER.info("VLM judge API ready (backend=%s, model=%s)", backend.kind, backend.model_id)
    return app


app = build_app()
