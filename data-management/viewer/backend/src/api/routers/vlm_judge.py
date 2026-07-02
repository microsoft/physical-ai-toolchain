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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator

from ..config import AppConfig, get_app_config
from ..csrf import require_csrf_token
from ..services.annotation_service import AnnotationService, get_annotation_service
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

_JUDGE_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vlm-judge")
_jobs: dict[str, JudgeJob] = {}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


PROCESS_METHODS = ("gvl", "chronological")
JOB_STATUSES = ("idle", "pending", "running", "done", "error")
_VIEW_NAME_RE = re.compile(SAFE_CAMERA_NAME_PATTERN)


class JudgeJob(BaseModel):
    cache_key: str
    status: str
    error: str | None = None
    result: dict[str, Any] | None = None


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
    job_status: str = "idle"
    judge_model: str | None = None
    prompt_version: str | None = None
    cache_key: str | None = None
    error: str | None = None
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
    cache_key: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$"),
    service: DatasetService = Depends(get_dataset_service),
    annotation_service: AnnotationService = Depends(get_annotation_service),
    config: AppConfig = Depends(get_app_config),
) -> JudgeStatus:
    """Return any cached judgment for ``(dataset_id, episode_idx)`` without inference."""
    judge_service = get_vlm_judge_service(config)
    if judge_service is None:
        return JudgeStatus(enabled=False, cached=False)

    record = _resolve_episode(service, dataset_id, episode_idx)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Episode {episode_idx} not found")

    instruction = await _resolve_judge_instruction(
        annotation_service=annotation_service,
        dataset_id=dataset_id,
        episode_idx=episode_idx,
        request_instruction=None,
        dataset_instruction=record.instruction,
    )
    cache = judge_service.cache_for(_judge_cache_dir(service, dataset_id))
    resolved_cache_key = cache_key or cache.key(
        video_paths=record.video_paths,
        instruction=instruction,
        judge_model=judge_service.model_id,
        prompt_version=_prompt_version(),
        from_s=record.from_timestamp,
        to_s=record.to_timestamp,
        agent_config=judge_service.config.agent,
    )
    job = _jobs.get(resolved_cache_key)
    active_job = job is not None and job.status in ("pending", "running", "error")
    cached_payload = None if active_job else cache.get(resolved_cache_key)
    result_payload = cached_payload or (job.result if job is not None and job.status == "done" else None)
    if cached_payload is not None:
        _jobs.pop(resolved_cache_key, None)
    job_status = "done" if result_payload is not None else job.status if job is not None else "idle"
    return JudgeStatus(
        enabled=True,
        cached=cached_payload is not None,
        job_status=job_status,
        judge_model=judge_service.model_id,
        prompt_version=_prompt_version(),
        cache_key=resolved_cache_key,
        error=job.error if job is not None and job.status == "error" else None,
        backend=judge_service.config.backend.kind,
        process_method=judge_service.config.agent.process_method,
        process_methods=list(PROCESS_METHODS),
        n_frames=judge_service.config.frames.n_frames,
        result=result_payload,
    )


@router.post(
    "/{dataset_id}/episodes/{episode_idx}/judge",
    response_model=JudgeStatus,
    dependencies=[Depends(require_csrf_token)],
)
async def run_episode_judgment(
    payload: JudgeRequest,
    response: Response,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0)),
    service: DatasetService = Depends(get_dataset_service),
    annotation_service: AnnotationService = Depends(get_annotation_service),
    config: AppConfig = Depends(get_app_config),
) -> JudgeStatus:
    """Start the VLM judge on ``(dataset_id, episode_idx)`` and poll via GET."""
    judge_service = get_vlm_judge_service(config)
    if judge_service is None:
        raise HTTPException(
            status_code=503,
            detail="VLM judge is disabled. Set VLM_JUDGE_ENABLED=true to enable.",
        )

    record = _resolve_episode(service, dataset_id, episode_idx, views=tuple(payload.views or ()))
    if record is None:
        raise HTTPException(status_code=404, detail=f"Episode {episode_idx} not found")

    instruction = await _resolve_judge_instruction(
        annotation_service=annotation_service,
        dataset_id=dataset_id,
        episode_idx=episode_idx,
        request_instruction=payload.instruction,
        dataset_instruction=record.instruction,
    )
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
    cached_payload = None if payload.force else cache.get(cache_key)
    if cached_payload is not None:
        _jobs.pop(cache_key, None)
        return _judge_status(
            judge_service=judge_service,
            cache_key=cache_key,
            cached_payload=cached_payload,
            job_status="done",
        )

    job = _jobs.get(cache_key)
    if job is None or job.status == "error":
        job = JudgeJob(cache_key=cache_key, status="pending")
        _jobs[cache_key] = job
        _JUDGE_EXECUTOR.submit(
            _run_judgment_job,
            job=job,
            judge_service=judge_service,
            dataset_id=dataset_id,
            episode_idx=episode_idx,
            instruction=instruction,
            video_paths=record.video_paths,
            from_s=record.from_timestamp,
            to_s=record.to_timestamp,
            force=payload.force,
            cache_dir=_judge_cache_dir(service, dataset_id),
            process_method=payload.process_method,
            effective_method=effective_method,
        )

    response.status_code = status.HTTP_202_ACCEPTED
    return _judge_status(
        judge_service=judge_service,
        cache_key=cache_key,
        cached_payload=None,
        job_status=job.status,
        error=job.error,
    )


def _run_judgment_job(
    *,
    job: JudgeJob,
    judge_service,
    dataset_id: str,
    episode_idx: int,
    instruction: str,
    video_paths: dict[str, Path],
    from_s: float | None,
    to_s: float | None,
    force: bool,
    cache_dir: Path,
    process_method: str | None,
    effective_method: str,
) -> None:
    job.status = "running"
    try:
        result = judge_service.judge_episode(
            episode_id=f"{dataset_id}/episode_{episode_idx:06d}",
            instruction=instruction,
            video_paths=video_paths,
            from_s=from_s,
            to_s=to_s,
            force=force,
            cache_dir=cache_dir,
            process_method=process_method,
        )
    except Exception as err:  # backend / model errors surface as 502
        safe_dataset_id = dataset_id.replace("\r", "").replace("\n", "")
        safe_episode_idx = int(episode_idx)
        logger.exception("VLM judge failed for %s/%d", safe_dataset_id, safe_episode_idx)
        job.status = "error"
        job.error = f"VLM backend error: {err}"
        return

    payload_out = result.to_dict()
    job.result = JudgeResponse(cached=False, process_method=effective_method, **payload_out).model_dump(mode="json")
    job.status = "done"


def _judge_status(
    *,
    judge_service,
    cache_key: str,
    cached_payload: dict[str, Any] | None,
    job_status: str,
    error: str | None = None,
) -> JudgeStatus:
    return JudgeStatus(
        enabled=True,
        cached=cached_payload is not None,
        job_status=job_status,
        judge_model=judge_service.model_id,
        prompt_version=_prompt_version(),
        cache_key=cache_key,
        error=error,
        backend=judge_service.config.backend.kind,
        process_method=judge_service.config.agent.process_method,
        process_methods=list(PROCESS_METHODS),
        n_frames=judge_service.config.frames.n_frames,
        result=cached_payload,
    )


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
        if "\x00" in part or part in ("", ".", "..") or "/" in part or "\\" in part or Path(part).name != part:
            raise HTTPException(
                status_code=400,
                detail="Path traversal detected: resolved path escapes dataset directory",
            )
    return parts


async def _resolve_judge_instruction(
    *,
    annotation_service: AnnotationService,
    dataset_id: str,
    episode_idx: int,
    request_instruction: str | None,
    dataset_instruction: str,
) -> str:
    request_value = (request_instruction or "").strip()
    if request_value:
        return request_value

    annotation_file = await annotation_service.get_annotation(dataset_id, episode_idx)
    if annotation_file is not None:
        saved: list[tuple[Any, str]] = []
        for annotation in annotation_file.annotations:
            language_instruction = annotation.language_instruction
            if language_instruction is None:
                continue
            instruction = language_instruction.instruction.strip()
            if instruction:
                saved.append((annotation.timestamp, instruction))
        if saved:
            return max(saved, key=lambda item: item[0])[1]

    return dataset_instruction or ""


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
