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
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import AppConfig, get_app_config
from ..csrf import require_csrf_token
from ..services.dataset_service import DatasetService, get_dataset_service
from ..services.vlm_judge_service import get_vlm_judge_service
from ..storage.paths import dataset_id_to_blob_prefix
from ..validation import (
    SAFE_DATASET_ID_PATTERN,
    SanitizedModel,
    path_int_param,
    path_string_param,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


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
    )
    force: bool = Field(default=False, description="Bypass the cache and re-run")


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

    cache_key = judge_service._cache.key(
        video_paths=record.video_paths,
        instruction=record.instruction,
        judge_model=judge_service.model_id,
        prompt_version=_prompt_version(),
        agent_config=judge_service.config.agent,
    )
    cached_payload = judge_service._cache.get(cache_key)
    return JudgeStatus(
        enabled=True,
        cached=cached_payload is not None,
        judge_model=judge_service.model_id,
        prompt_version=_prompt_version(),
        cache_key=cache_key,
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

    # Detect cache hit before invoking the backend so we can flag it on the wire.
    cache_key = judge_service._cache.key(
        video_paths=record.video_paths,
        instruction=instruction,
        judge_model=judge_service.model_id,
        prompt_version=_prompt_version(),
        agent_config=judge_service.config.agent,
    )
    was_cached = not payload.force and judge_service._cache.get(cache_key) is not None

    try:
        result = judge_service.judge_episode(
            episode_id=f"{dataset_id}/episode_{episode_idx:06d}",
            instruction=instruction,
            video_paths=record.video_paths,
            from_s=record.from_timestamp,
            to_s=record.to_timestamp,
            force=payload.force,
        )
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except Exception as err:  # backend / model errors surface as 502
        logger.exception("VLM judge failed for %s/%d", dataset_id, episode_idx)
        raise HTTPException(status_code=502, detail=f"VLM backend error: {err}") from err

    payload_out = result.to_dict()
    return JudgeResponse(cached=was_cached, **payload_out)


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
    # Dataset IDs use '--' as a separator that maps to nested directories on disk
    # (e.g. "hybrid-hack--session_xyz" -> "hybrid-hack/session_xyz").
    dataset_root = Path(base_path) / dataset_id_to_blob_prefix(dataset_id)
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


def _prompt_version() -> str:
    from evaluation.vlm_judge.prompts import PROMPT_VERSION

    return PROMPT_VERSION
