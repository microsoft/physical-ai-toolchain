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
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..auth import PrincipalContext, require_principal_context
from ..services.annotation_service import AnnotationService, get_annotation_service
from ..services.saved_input_service import SavedInputError, SavedInputSnapshot, resolve_saved_input
from ..storage import StorageError

if TYPE_CHECKING:
    from evaluation.vlm_judge.dataset import EpisodeRecord

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

    model_config = ConfigDict(extra="forbid")
    snapshot_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    annotation_author_id: str | None = Field(default=None, min_length=1, max_length=256)

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
    snapshot_id: str | None = None
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


@router.get("/{dataset_id}/episodes/{episode_idx}/judge/snapshot", response_model=SavedInputSnapshot)
async def get_saved_input_snapshot(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0)),
    annotation_author_id: str | None = None,
    principal: PrincipalContext = Depends(require_principal_context),
    service: DatasetService = Depends(get_dataset_service),
    annotations: AnnotationService = Depends(get_annotation_service),
) -> SavedInputSnapshot:
    _, snapshot = await _resolve_saved_episode(
        service, annotations, dataset_id, episode_idx, principal.scope_id, annotation_author_id=annotation_author_id
    )
    return snapshot


@router.get(
    "/{dataset_id}/episodes/{episode_idx}/judge",
    response_model=JudgeStatus,
)
async def get_episode_judgment(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0)),
    service: DatasetService = Depends(get_dataset_service),
    config: AppConfig = Depends(get_app_config),
    principal: PrincipalContext = Depends(require_principal_context),
    annotations: AnnotationService = Depends(get_annotation_service),
) -> JudgeStatus:
    """Return any cached judgment for ``(dataset_id, episode_idx)`` without inference."""
    judge_service = get_vlm_judge_service(config)
    if judge_service is None:
        return JudgeStatus(enabled=False, cached=False)

    record, snapshot = await _resolve_saved_episode(service, annotations, dataset_id, episode_idx, principal.scope_id)

    cache = judge_service.cache_for(_judge_cache_dir(service, dataset_id) / snapshot.snapshot_id)
    cache_key = cache.key(
        video_paths=record.video_paths,
        instruction=snapshot.instruction,
        judge_model=judge_service.model_id,
        prompt_version=_prompt_version(),
        from_s=record.from_timestamp,
        to_s=record.to_timestamp,
        agent_config=judge_service.config.agent,
        video_windows=record.video_windows,
        media_identity=record.media_identity,
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
    principal: PrincipalContext = Depends(require_principal_context),
    annotations: AnnotationService = Depends(get_annotation_service),
) -> JudgeResponse:
    """Run the VLM judge on ``(dataset_id, episode_idx)`` (cache-first)."""
    judge_service = get_vlm_judge_service(config)
    if judge_service is None:
        raise HTTPException(
            status_code=503,
            detail="VLM judge is disabled. Set VLM_JUDGE_ENABLED=true to enable.",
        )

    if payload.instruction is not None:
        raise HTTPException(status_code=422, detail="Use a saved instruction; request-body overrides are not supported")
    record, snapshot = await _resolve_saved_episode(
        service,
        annotations,
        dataset_id,
        episode_idx,
        principal.scope_id,
        views=tuple(payload.views or ()),
        annotation_author_id=payload.annotation_author_id,
        expected_snapshot_id=payload.snapshot_id,
    )
    instruction = snapshot.instruction
    cache_dir = _judge_cache_dir(service, dataset_id) / snapshot.snapshot_id

    if payload.process_method is not None and payload.process_method not in PROCESS_METHODS:
        raise HTTPException(
            status_code=422,
            detail=f"process_method must be one of {list(PROCESS_METHODS)}",
        )
    effective_method = payload.process_method or judge_service.config.agent.process_method

    # Detect cache hit before invoking the backend so we can flag it on the wire.
    cache = judge_service.cache_for(cache_dir)
    cache_key = cache.key(
        video_paths=record.video_paths,
        instruction=instruction,
        judge_model=judge_service.model_id,
        prompt_version=_prompt_version(),
        from_s=record.from_timestamp,
        to_s=record.to_timestamp,
        agent_config=replace(judge_service.config.agent, process_method=effective_method),
        video_windows=record.video_windows,
        media_identity=record.media_identity,
    )
    was_cached = not payload.force and cache.get(cache_key) is not None

    try:
        if not was_cached:
            record = replace(
                record, video_paths=await service.materialize_episode_media(dataset_id, record.video_paths)
            )
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
            cache_dir=cache_dir,
            process_method=payload.process_method,
            video_windows=record.video_windows,
            media_identity=record.media_identity,
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

    await _resolve_saved_episode(
        service,
        annotations,
        dataset_id,
        episode_idx,
        principal.scope_id,
        views=tuple(payload.views or ()),
        annotation_author_id=payload.annotation_author_id,
        expected_snapshot_id=snapshot.snapshot_id,
    )
    payload_out = result.to_dict()
    return JudgeResponse(
        snapshot_id=snapshot.snapshot_id, cached=was_cached, process_method=effective_method, **payload_out
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_saved_episode(
    service: DatasetService,
    annotations: AnnotationService,
    dataset_id: str,
    episode_idx: int,
    principal_scope_id: str,
    *,
    views: tuple[str, ...] = (),
    annotation_author_id: str | None = None,
    expected_snapshot_id: str | None = None,
) -> tuple[EpisodeRecord, SavedInputSnapshot]:
    _dataset_path_parts(dataset_id)
    if await service.get_dataset(dataset_id) is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    try:
        source = await service.get_source_revision(dataset_id, episode_idx)
        record = await service.get_episode_media_record(dataset_id, episode_idx)
        if record is None:
            raise HTTPException(status_code=404, detail="Episode not found")
        snapshot = await resolve_saved_input(
            service,
            annotations,
            dataset_id,
            episode_idx,
            principal_scope_id=principal_scope_id,
            source=source,
            dataset_instruction=record.instruction,
            media_identity=record.media_identity,
            video_windows=record.video_windows,
            annotation_author_id=annotation_author_id,
            expected_snapshot_id=expected_snapshot_id,
        )
        verified = await service.get_episode_media_record(dataset_id, episode_idx)
        if verified is None or (verified.media_identity, verified.video_windows, verified.instruction) != (
            record.media_identity,
            record.video_windows,
            record.instruction,
        ):
            raise SavedInputError("Media changed while resolving saved inputs")
        if views:
            if not set(views).issubset(record.video_paths):
                raise SavedInputError("Unknown camera selection", 422)
            record = replace(
                record,
                video_paths={view: record.video_paths[view] for view in views},
                video_windows={view: window for view, window in record.video_windows.items() if view in views},
                media_identity={view: record.media_identity[view] for view in views} if record.media_identity else None,
            )
        return record, snapshot
    except SavedInputError as error:
        logger.warning("Saved judge input rejected for episode %d", episode_idx)
        raise HTTPException(status_code=error.status_code, detail=str(error)) from None
    except ValueError:
        raise HTTPException(status_code=409, detail="Saved input source changed or is unavailable") from None
    except StorageError:
        logger.error("Saved judge input storage unavailable")
        raise HTTPException(status_code=503, detail="Saved input storage unavailable") from None


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
