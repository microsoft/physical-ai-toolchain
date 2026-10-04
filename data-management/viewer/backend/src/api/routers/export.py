"""
Export API endpoints for episode data with edit operations.

Exports HDF5 sources to new HDF5 files and LeRobot v3.0 sources to a new
LeRobot v3.0 dataset, with frame editing, removal, and sub-task annotations applied.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, FiniteFloat, NonNegativeInt

from ..csrf import require_csrf_token
from ..models.annotations import EpisodeAnnotationFile
from ..services.annotation_service import AnnotationService, get_annotation_service
from ..services.dataset_service import DatasetService, get_dataset_service
from ..services.episode_edits import (
    EpisodeEditOperations,
    ExportError,
    ExportProgress,
    ExportResult,
    parse_edit_operations,
)
from ..services.hdf5_exporter import HDF5Exporter
from ..services.lerobot_exporter import LeRobotExporter, admits_export
from ..services.lerobot_language import LanguageInstruction
from ..validation import (
    SAFE_DATASET_ID_PATTERN,
    SanitizedModel,
    path_string_param,
    query_csv_ints_param,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class ImageTransformRequest(SanitizedModel):
    """Image transform request model."""

    crop: dict[str, int] | None = Field(
        None,
        description="Crop region with x, y, width, height",
        examples=[{"x": 10, "y": 10, "width": 200, "height": 150}],
    )
    resize: dict[str, int] | None = Field(
        None,
        description="Resize dimensions with width, height",
        examples=[{"width": 224, "height": 224}],
    )


class SubtaskRequest(SanitizedModel):
    """Subtask segment request model."""

    id: str = Field(..., description="Unique segment ID")
    label: str = Field(..., description="Human-readable label")
    frameRange: list[int] = Field(
        ...,
        description="Frame range [start, end] inclusive",
        min_length=2,
        max_length=2,
    )
    color: str = Field(..., description="Display color (hex)")
    source: str = Field("manual", description="'manual' or 'auto'")
    description: str | None = None


class FrameInsertionRequest(SanitizedModel):
    """Frame insertion request model."""

    afterFrameIndex: int = Field(..., ge=0, description="Insert after this frame index")
    interpolationFactor: float = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="Interpolation factor (0.0-1.0)",
    )


class TrajectoryAdjustmentRequest(SanitizedModel):
    """Joint-position adjustment at one frame; a set value replaces that channel's delta."""

    frameIndex: NonNegativeInt = Field(..., description="Original frame index")
    channelDeltas: dict[NonNegativeInt, FiniteFloat] | None = Field(None, description="Values added to channels")
    channelValues: dict[NonNegativeInt, FiniteFloat] | None = Field(None, description="Values that replace channels")


class EpisodeEditRequest(SanitizedModel):
    """Edit operations for a single episode."""

    episodeIndex: int = Field(..., description="Episode index")
    globalTransform: ImageTransformRequest | None = Field(None, description="Transform applied to all cameras")
    cameraTransforms: dict[str, ImageTransformRequest] | None = Field(
        None, description="Per-camera transform overrides"
    )
    removedFrames: list[int] | None = Field(None, description="Frame indices to exclude")
    insertedFrames: list[FrameInsertionRequest] | None = Field(None, description="Interpolated frame insertions")
    subtasks: list[SubtaskRequest] | None = Field(None, description="Sub-task segments")
    trajectoryAdjustments: list[TrajectoryAdjustmentRequest] | None = Field(
        None, description="Joint-position adjustments exported as qpos_adjusted beside the recorded qpos"
    )


def _edit_operations(dataset_id: str, edit_req: EpisodeEditRequest) -> EpisodeEditOperations:
    """Convert one episode's validated edit request into exporter edit operations."""
    return parse_edit_operations(
        {
            "datasetId": dataset_id,
            "episodeIndex": edit_req.episodeIndex,
            "globalTransform": edit_req.globalTransform.model_dump() if edit_req.globalTransform else None,
            "cameraTransforms": {k: v.model_dump() for k, v in edit_req.cameraTransforms.items()}
            if edit_req.cameraTransforms
            else None,
            "removedFrames": edit_req.removedFrames,
            "insertedFrames": [i.model_dump() for i in edit_req.insertedFrames] if edit_req.insertedFrames else None,
            "subtasks": [s.model_dump() for s in edit_req.subtasks] if edit_req.subtasks is not None else None,
            "trajectoryAdjustments": [a.model_dump() for a in edit_req.trajectoryAdjustments]
            if edit_req.trajectoryAdjustments
            else None,
        }
    )


class ExportRequest(SanitizedModel):
    """Export request model."""

    episodeIndices: list[int] = Field(..., description="Episode indices to export", min_length=1)
    outputPath: str = Field(..., description="Output directory path")
    applyEdits: bool = Field(True, description="Whether to apply edit operations")
    edits: dict[int, EpisodeEditRequest] | None = Field(None, description="Edit operations by episode index")
    includeLanguageInstructions: bool = Field(
        True,
        description=(
            "For LeRobot sources, write each episode's latest saved instruction as task_aug and plan rows; "
            "send false to leave them out"
        ),
    )


class ExportResultResponse(BaseModel):
    """Batch result with public error text and aggregate statistics on success or failure."""

    success: bool
    outputFiles: list[str]
    error: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)


def _prepare_output(service: DatasetService, dataset_id: str, dataset_path: Path, output_path: Path) -> bool:
    """Validate the output path for the source's format and return whether the source is a LeRobot dataset.

    A LeRobot export writes a new dataset, so its path must be new or empty and must neither sit inside
    nor contain the source. What a stopped export left, a lock file or a claim, goes on to the exporter,
    which recovers or refuses it. These checks run before anything is created; an HDF5 export creates its
    directory.
    """
    if service.dataset_is_lerobot(dataset_id):
        if output_path.is_relative_to(dataset_path) or dataset_path.is_relative_to(output_path):
            raise HTTPException(
                status_code=400,
                detail="Output path must be outside the source dataset and must not contain it",
            )
        if not admits_export(output_path):
            raise HTTPException(
                status_code=400,
                detail="Output path must be a new or empty directory for a LeRobot export",
            )
        return True
    try:
        output_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid output path: {e}",
        )
    return False


def _latest_instruction(annotation_file: EpisodeAnnotationFile | None) -> LanguageInstruction | None:
    """Return the most recently saved language instruction among an episode's annotations, if any."""
    saved = [
        annotation
        for annotation in (annotation_file.annotations if annotation_file else [])
        if annotation.language_instruction
    ]
    if not saved:
        return None
    latest = max(saved, key=lambda annotation: annotation.timestamp)
    language = latest.language_instruction
    return LanguageInstruction(
        language.instruction,
        tuple(language.paraphrases),
        tuple(language.subtask_instructions),
        latest.annotator_id,
        latest.timestamp.isoformat(),
    )


async def _export_options(
    dataset_id: str, request: ExportRequest, lerobot: bool, annotations: AnnotationService
) -> dict[str, Any]:
    """Return the exporter's options: parsed edits and, when requested for LeRobot, the saved instructions."""
    edits_map = None
    if request.applyEdits and request.edits:
        edits_map = {index: _edit_operations(dataset_id, edit_req) for index, edit_req in request.edits.items()}
    options: dict[str, Any] = {"episode_indices": request.episodeIndices, "edits_map": edits_map}
    if lerobot:
        language = {}
        if request.includeLanguageInstructions:
            for index in request.episodeIndices:
                instruction = _latest_instruction(await annotations.get_annotation(dataset_id, index))
                if instruction is not None:
                    language[index] = instruction
        options["language"] = language
    return options


def _exporter(lerobot: bool, dataset_id: str, dataset_path: Path, output_path: Path) -> HDF5Exporter | LeRobotExporter:
    if lerobot:
        return LeRobotExporter(dataset_path, output_path, dataset_id=dataset_id)
    return HDF5Exporter(dataset_path, output_path)


def _public_export_result(result: ExportResult) -> ExportResultResponse:
    if not result.success:
        logger.error("Export failed: %s", result.error)
    return ExportResultResponse(
        success=result.success,
        outputFiles=result.output_files,
        error=None if result.success else "Export failed",
        stats=result.stats,
    )


@router.post(
    "/{dataset_id}/export",
    response_model=ExportResultResponse,
    dependencies=[Depends(require_csrf_token)],
)
async def export_episodes(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    request: ExportRequest = ...,
    service: DatasetService = Depends(get_dataset_service),
    annotations: AnnotationService = Depends(get_annotation_service),
) -> ExportResultResponse:
    """
    Export episodes with edit operations applied.

    LeRobot v3.0 sources produce a new LeRobot dataset at the output path. HDF5 sources
    produce new HDF5 files in the specified output directory with:
    - Frame removal applied (excluded frames are not written)
    - Image transforms applied (crop/resize)
    - Metadata JSON file with edit history
    - Subtask JSON file if segments are defined

    This is a synchronous endpoint. For progress updates, use the
    /export/stream endpoint with SSE.
    """
    # Validate dataset exists
    dataset = await service.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")

    # Get dataset path
    try:
        dataset_path = service._get_dataset_path(dataset_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Dataset '{dataset_id}' does not have a valid path for export",
        )
    safe_dataset_base = os.path.realpath(service.base_path)
    resolved_dataset = os.path.realpath(str(dataset_path))
    if not resolved_dataset.startswith(safe_dataset_base + os.sep):
        raise HTTPException(
            status_code=400,
            detail="Path traversal detected: dataset path escapes base directory",
        )
    dataset_path = Path(resolved_dataset)
    if not dataset_path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Dataset '{dataset_id}' does not have a local path for export",
        )

    # Validate output path
    safe_base = os.path.realpath(service.base_path)
    output_path_str = os.path.realpath(request.outputPath)
    if not output_path_str.startswith(safe_base + os.sep):
        raise HTTPException(
            status_code=400,
            detail="Path traversal detected: resolved path escapes base directory",
        )
    output_path = Path(output_path_str)
    lerobot = _prepare_output(service, dataset_id, dataset_path, output_path)

    try:
        exporter = _exporter(lerobot, dataset_id, dataset_path, output_path)
        options = await _export_options(dataset_id, request, lerobot, annotations)
        result = await run_in_threadpool(exporter.export_episodes, **options)

        return _public_export_result(result)

    except ImportError as e:
        raise HTTPException(
            status_code=501,
            detail=f"Export not available: {e}",
        )
    except ExportError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Export failed: {e}",
        )


@router.post("/{dataset_id}/export/stream", dependencies=[Depends(require_csrf_token)])
async def export_episodes_stream(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    request: ExportRequest = ...,
    service: DatasetService = Depends(get_dataset_service),
    annotations: AnnotationService = Depends(get_annotation_service),
) -> StreamingResponse:
    """
    Export episodes with SSE progress streaming.

    Returns a Server-Sent Events stream with progress updates:
    - event: progress - Current export progress
    - event: complete - Export finished
    - event: error - Export failed

    Each progress event contains:
    - currentEpisode: int
    - totalEpisodes: int
    - currentFrame: int
    - totalFrames: int
    - percentage: float (0-100)
    - status: str
    """
    # Validate dataset exists
    dataset = await service.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")

    # Get dataset path
    try:
        dataset_path = service._get_dataset_path(dataset_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Dataset '{dataset_id}' does not have a valid path for export",
        )
    safe_stream_base = os.path.realpath(service.base_path)
    resolved_stream = os.path.realpath(str(dataset_path))
    if not resolved_stream.startswith(safe_stream_base + os.sep):
        raise HTTPException(
            status_code=400,
            detail="Path traversal detected: dataset path escapes base directory",
        )
    dataset_path = Path(resolved_stream)
    if not dataset_path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Dataset '{dataset_id}' does not have a local path for export",
        )

    # Validate output path
    safe_base = os.path.realpath(service.base_path)
    output_path_str = os.path.realpath(request.outputPath)
    if not output_path_str.startswith(safe_base + os.sep):
        raise HTTPException(
            status_code=400,
            detail="Path traversal detected: resolved path escapes base directory",
        )
    output_path = Path(output_path_str)
    lerobot = _prepare_output(service, dataset_id, dataset_path, output_path)

    async def event_generator():
        try:
            exporter = _exporter(lerobot, dataset_id, dataset_path, output_path)
            options = await _export_options(dataset_id, request, lerobot, annotations)

            # Queue for progress updates
            progress_queue: asyncio.Queue[ExportProgress | None] = asyncio.Queue()

            def progress_callback(progress: ExportProgress):
                # Non-blocking put
                import contextlib

                with contextlib.suppress(asyncio.QueueFull):
                    progress_queue.put_nowait(progress)

            # Run export in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            export_task = loop.run_in_executor(
                None,
                lambda: exporter.export_episodes(**options, progress_callback=progress_callback),
            )

            # Stream progress updates
            while not export_task.done():
                try:
                    progress = await asyncio.wait_for(
                        progress_queue.get(),
                        timeout=0.5,
                    )
                    if progress:
                        progress_data = {
                            "currentEpisode": progress.current_episode,
                            "totalEpisodes": progress.total_episodes,
                            "currentFrame": progress.current_frame,
                            "totalFrames": progress.total_frames,
                            "percentage": progress.percentage,
                            "status": progress.status,
                        }
                        yield f"event: progress\ndata: {json.dumps(progress_data)}\n\n"
                except TimeoutError:
                    continue

            # Get final result
            result = await export_task

            # Drain any remaining progress updates
            while not progress_queue.empty():
                try:
                    progress = progress_queue.get_nowait()
                    if progress:
                        progress_data = {
                            "currentEpisode": progress.current_episode,
                            "totalEpisodes": progress.total_episodes,
                            "currentFrame": progress.current_frame,
                            "totalFrames": progress.total_frames,
                            "percentage": progress.percentage,
                            "status": progress.status,
                        }
                        yield f"event: progress\ndata: {json.dumps(progress_data)}\n\n"
                except asyncio.QueueEmpty:
                    break

            # Send completion event
            complete_data = _public_export_result(result).model_dump()
            yield f"event: complete\ndata: {json.dumps(complete_data)}\n\n"

        except ImportError:
            logger.exception("Export stream unavailable")
            payload = {"code": "EXPORT_UNAVAILABLE", "message": "Export is unavailable"}
            yield f"event: error\ndata: {json.dumps(payload)}\n\n"
        except Exception:
            logger.exception("Export stream failed")
            payload = {"code": "EXPORT_FAILED", "message": "Export failed"}
            yield f"event: error\ndata: {json.dumps(payload)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{dataset_id}/export/preview")
async def preview_export(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_indices: list[int] = Depends(
        query_csv_ints_param("episode_indices", description="Comma-separated episode indices")
    ),
    removed_frames: set[int] = Depends(
        query_csv_ints_param(
            "removed_frames",
            description="Comma-separated frame indices to remove",
            required=False,
            as_set=True,
        )
    ),
    service: DatasetService = Depends(get_dataset_service),
) -> dict[str, Any]:
    """
    Preview export without writing files.

    Returns statistics about what would be exported:
    - Total frames
    - Frames to be removed
    - Output frame count

    Useful for confirming export settings before running.
    """
    # Validate dataset exists
    dataset = await service.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")

    # Calculate preview stats
    total_original_frames = 0
    total_output_frames = 0

    for episode_idx in episode_indices:
        episode = await service.get_episode(dataset_id, episode_idx)
        if episode:
            original = episode.meta.length
            output = original - len([frame for frame in removed_frames if frame < original])
            total_original_frames += original
            total_output_frames += output

    return {
        "episodeCount": len(episode_indices),
        "originalFrames": total_original_frames,
        "removedFrames": total_original_frames - total_output_frames,
        "outputFrames": total_output_frames,
        "estimatedSizeMb": total_output_frames * 0.1,  # Rough estimate
    }
