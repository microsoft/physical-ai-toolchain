"""VLM-as-judge endpoints for the dataviewer.

Resolves a dataset + episode pair to view-aligned MP4 paths via the same
``evaluation.vlm_judge.dataset.iter_episodes`` walker that drives the CLI,
then delegates to a singleton :class:`evaluation.vlm_judge.JudgeService`.

Endpoints (all under ``/api/datasets``, mounted with auth):

- ``GET    /{dataset_id}/episodes/{episode_idx}/judge`` — return cached judgment if any.
- ``POST   /{dataset_id}/episodes/{episode_idx}/judge`` — run the judge (cached or fresh).

The cache is keyed on (video paths + size + mtime, instruction, judge_model,
prompt_version, agent_config) so re-running over the same episode is free.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator

from ..config import AppConfig, get_app_config
from ..csrf import require_csrf_token
from ..services.dataset_service import DatasetService, get_dataset_service
from ..services.vlm_judge_service import get_vlm_judge_service
from ..validation import (
    SAFE_CAMERA_NAME_PATTERN,
    SAFE_DATASET_ID_PATTERN,
    SanitizedModel,
    path_int_param,
    path_string_param,
    sanitize_user_string,
    validate_path_containment,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


PROCESS_METHODS = ("gvl", "chronological")
_VIEW_NAME_RE = re.compile(SAFE_CAMERA_NAME_PATTERN)


class JudgeRequest(SanitizedModel):
    """Optional overrides on a per-call basis."""

    instruction: str | None = Field(
        default=None,
        description="Override the dataset-supplied instruction",
        max_length=1024,
    )
    views: list[str] | None = Field(
        default=None,
        description="Subset of view names to evaluate (default: all video features)",
        max_length=16,
    )
    process_method: str | None = Field(
        default=None,
        description="Process-reward scoring technique: 'gvl' or 'chronological'",
    )
    force: bool = Field(default=False, description="Bypass the cache and re-run")

    @field_validator("views")
    @classmethod
    def validate_views(cls, views: list[str] | None) -> list[str] | None:
        if views is None:
            return None
        return [_validate_view_name(view) for view in views]


class MilestoneOut(BaseModel):
    name: str
    completed: bool
    frame_range: str
    evidence: str = ""


class JudgeStatus(BaseModel):
    """``GET`` response — describes what would run + any cached result."""

    enabled: bool
    cached: bool
    judge_model: str | None = None
    prompt_version: str | None = None
    cache_key: str | None = None
    backend: str | None = None
    process_method: str | None = None
    process_methods: list[str] = []
    n_frames: int | None = None
    result: dict[str, Any] | None = None


class JudgeResponse(BaseModel):
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
    milestones: list[MilestoneOut] = []
    failure_mode: str | None = None
    process_method: str | None = None
    cached: bool = False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{dataset_id}/episodes/{episode_idx}/judge",
    response_model=JudgeStatus,
)
async def get_episode_judgment(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0)),
    service: DatasetService = Depends(get_dataset_service),
    config: AppConfig = Depends(get_app_config),
) -> JudgeStatus:
    """Return any cached judgment for ``(dataset_id, episode_idx)`` without inference."""
    judge_service = get_vlm_judge_service(config)
    if judge_service is None:
        return JudgeStatus(enabled=False, cached=False)

    record = _resolve_episode(service, dataset_id, episode_idx)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Episode {episode_idx} not found")

    cache = judge_service.cache_for(_judge_cache_dir(service, dataset_id))
    cache_key = cache.key(
        video_paths=record.video_paths,
        instruction=record.instruction,
        judge_model=judge_service.model_id,
        prompt_version=_prompt_version(),
        from_s=record.from_timestamp,
        to_s=record.to_timestamp,
        agent_config=judge_service.config.agent,
    )
    cached_payload = cache.get(cache_key)
    return JudgeStatus(
        enabled=True,
        cached=cached_payload is not None,
        judge_model=judge_service.model_id,
        prompt_version=_prompt_version(),
        cache_key=cache_key,
        backend=judge_service.config.backend.kind,
        process_method=judge_service.config.agent.process_method,
        process_methods=list(PROCESS_METHODS),
        n_frames=judge_service.config.frames.n_frames,
        result=cached_payload,
    )


@router.post(
    "/{dataset_id}/episodes/{episode_idx}/judge",
    response_model=JudgeResponse,
    dependencies=[Depends(require_csrf_token)],
)
async def run_episode_judgment(
    payload: JudgeRequest,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0)),
    service: DatasetService = Depends(get_dataset_service),
    config: AppConfig = Depends(get_app_config),
) -> JudgeResponse:
    """Run the VLM judge on ``(dataset_id, episode_idx)`` (cache-first)."""
    judge_service = get_vlm_judge_service(config)
    if judge_service is None:
        raise HTTPException(
            status_code=503,
            detail="VLM judge is disabled. Set VLM_JUDGE_ENABLED=true to enable.",
        )

    record = _resolve_episode(service, dataset_id, episode_idx, views=tuple(payload.views or ()))
    if record is None:
        raise HTTPException(status_code=404, detail=f"Episode {episode_idx} not found")

    instruction = payload.instruction or record.instruction or ""
    if not instruction:
        raise HTTPException(
            status_code=422,
            detail="No task instruction available; provide one via the request body",
        )

    if payload.process_method is not None and payload.process_method not in PROCESS_METHODS:
        raise HTTPException(
            status_code=422,
            detail=f"process_method must be one of {list(PROCESS_METHODS)}",
        )
    effective_method = payload.process_method or judge_service.config.agent.process_method

    # Detect cache hit before invoking the backend so we can flag it on the wire.
    cache = judge_service.cache_for(_judge_cache_dir(service, dataset_id))
    cache_key = cache.key(
        video_paths=record.video_paths,
        instruction=instruction,
        judge_model=judge_service.model_id,
        prompt_version=_prompt_version(),
        from_s=record.from_timestamp,
        to_s=record.to_timestamp,
        agent_config=replace(judge_service.config.agent, process_method=effective_method),
    )
    was_cached = not payload.force and cache.get(cache_key) is not None

    try:
        # Model inference is blocking and GPU-bound; run it in a worker thread so
        # the single event loop stays free to serve episode/video requests while
        # a judgment is in flight (otherwise the whole backend stalls per run).
        result = await run_in_threadpool(
            judge_service.judge_episode,
            episode_id=f"{dataset_id}/episode_{episode_idx:06d}",
            instruction=instruction,
            video_paths=record.video_paths,
            from_s=record.from_timestamp,
            to_s=record.to_timestamp,
            force=payload.force,
            cache_dir=_judge_cache_dir(service, dataset_id),
            process_method=payload.process_method,
        )
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except Exception as err:  # backend / model errors surface as 502
        safe_dataset_id = dataset_id.replace("\r", "").replace("\n", "")
        safe_episode_idx = int(episode_idx)
        logger.exception("VLM judge failed for %s/%d", safe_dataset_id, safe_episode_idx)
        raise HTTPException(status_code=502, detail=f"VLM backend error: {err}") from err

    payload_out = result.to_dict()
    return JudgeResponse(cached=was_cached, process_method=effective_method, **payload_out)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_episode(
    service: DatasetService,
    dataset_id: str,
    episode_idx: int,
    *,
    views: tuple[str, ...] = (),
):
    """Return the matching ``EpisodeRecord`` or ``None`` if not found."""
    from evaluation.vlm_judge.dataset import iter_episodes

    base_path = getattr(service, "base_path", None)
    if not base_path:
        raise HTTPException(
            status_code=503,
            detail="VLM judge requires a local dataset path (base_path)",
        )
    dataset_root = _dataset_root(service, dataset_id)
    if not dataset_root.exists():
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")

    try:
        for record in iter_episodes(
            dataset_root,
            views=views or None,
            indices=[episode_idx],
            limit=1,
        ):
            return record
    except (FileNotFoundError, ValueError) as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return None


def _dataset_root(service: DatasetService, dataset_id: str) -> Path:
    """Resolve the on-disk root of ``dataset_id`` under the local data dir."""
    base_path = getattr(service, "base_path", None)
    if not base_path:
        raise HTTPException(
            status_code=503,
            detail="VLM judge requires a local dataset path (base_path)",
        )
    base_root = validate_path_containment(Path(base_path), Path(base_path))
    root = validate_path_containment(base_root.joinpath(*_dataset_path_parts(dataset_id)), base_root)
    return root


def _dataset_path_parts(dataset_id: str) -> tuple[str, ...]:
    sanitized = sanitize_user_string(dataset_id)
    parts = tuple(sanitized.split("--"))
    if not parts:
        raise HTTPException(status_code=400, detail="Invalid dataset_id")
    for part in parts:
        if (
            "\x00" in part
            or part in ("", ".", "..")
            or "/" in part
            or "\\" in part
            or Path(part).name != part
        ):
            raise HTTPException(
                status_code=400,
                detail="Path traversal detected: resolved path escapes dataset directory",
            )
    return parts


def _validate_view_name(view: str) -> str:
    sanitized = sanitize_user_string(view)
    if (
        "\x00" in sanitized
        or sanitized in (".", "..")
        or "/" in sanitized
        or "\\" in sanitized
        or not sanitized.strip()
        or _VIEW_NAME_RE.fullmatch(sanitized) is None
    ):
        raise ValueError(f"Invalid view: {sanitized!r}")
    return sanitized


def _judge_cache_dir(service: DatasetService, dataset_id: str) -> Path:
    """Per-dataset judgment cache, stored beside the dataset's annotations.

    Results live under ``<dataset>/annotations/vlm_judge/`` so they travel with
    the dataset and are reused on subsequent runs over the same episode.
    """
    return _dataset_root(service, dataset_id) / "annotations" / "vlm_judge"


def _prompt_version() -> str:
    from evaluation.vlm_judge.prompts import PROMPT_VERSION

    return PROMPT_VERSION
