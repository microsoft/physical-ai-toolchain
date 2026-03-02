"""
Export API endpoints for episode data with edit operations.

Provides endpoints for exporting episodes to HDF5 files with
frame editing, removal, and sub-task annotations applied.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..services.dataset_service import DatasetService, get_dataset_service
from ..services.hdf5_exporter import (
    EpisodeEditOperations,
    ExportProgress,
    HDF5Exporter,
    HDF5ExportError,
    parse_edit_operations,
)

router = APIRouter()


class ImageTransformRequest(BaseModel):
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


class SubtaskRequest(BaseModel):
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


class FrameInsertionRequest(BaseModel):
    """Frame insertion request model."""

    afterFrameIndex: int = Field(..., ge=0, description="Insert after this frame index")
    interpolationFactor: float = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="Interpolation factor (0.0-1.0)",
    )


class EpisodeEditRequest(BaseModel):
    """Edit operations for a single episode."""

    episodeIndex: int = Field(..., description="Episode index")
    globalTransform: ImageTransformRequest | None = Field(None, description="Transform applied to all cameras")
    cameraTransforms: dict[str, ImageTransformRequest] | None = Field(
        None, description="Per-camera transform overrides"
    )
    removedFrames: list[int] | None = Field(None, description="Frame indices to exclude")
    insertedFrames: list[FrameInsertionRequest] | None = Field(None, description="Interpolated frame insertions")
    subtasks: list[SubtaskRequest] | None = Field(None, description="Sub-task segments")


class ExportRequest(BaseModel):
    """Export request model."""

    episodeIndices: list[int] = Field(..., description="Episode indices to export", min_length=1)
    outputPath: str = Field(..., description="Output directory path")
    applyEdits: bool = Field(True, description="Whether to apply edit operations")
    edits: dict[int, EpisodeEditRequest] | None = Field(None, description="Edit operations by episode index")


class ExportResultResponse(BaseModel):
    """Export result response model."""

    success: bool
    outputFiles: list[str]
    error: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)


@router.post("/{dataset_id}/export", response_model=ExportResultResponse)
async def export_episodes(
    dataset_id: str,
    request: ExportRequest,
    service: DatasetService = Depends(get_dataset_service),
) -> ExportResultResponse:
    """
    Export episodes to new HDF5 files with edit operations applied.

    Creates new HDF5 files in the specified output directory with:
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
    dataset_path = service._get_dataset_path(dataset_id)
    if not dataset_path:
        raise HTTPException(
            status_code=400,
            detail=f"Dataset '{dataset_id}' does not have a local path for export",
        )

    # Validate output path
    output_path = Path(request.outputPath)
    try:
        output_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid output path: {e}",
        )

    try:
        exporter = HDF5Exporter(dataset_path, output_path)

        # Parse edit operations
        edits_map: dict[int, EpisodeEditOperations] | None = None
        if request.applyEdits and request.edits:
            edits_map = {}
            for episode_idx, edit_req in request.edits.items():
                # Convert string key to int (JSON keys are always strings)
                idx = int(episode_idx) if isinstance(episode_idx, str) else episode_idx
                edits_map[idx] = parse_edit_operations(
                    {
                        "datasetId": dataset_id,
                        "episodeIndex": edit_req.episodeIndex,
                        "globalTransform": edit_req.globalTransform.model_dump() if edit_req.globalTransform else None,
                        "cameraTransforms": {k: v.model_dump() for k, v in edit_req.cameraTransforms.items()}
                        if edit_req.cameraTransforms
                        else None,
                        "removedFrames": edit_req.removedFrames,
                        "insertedFrames": [i.model_dump() for i in edit_req.insertedFrames]
                        if edit_req.insertedFrames
                        else None,
                        "subtasks": [s.model_dump() for s in edit_req.subtasks] if edit_req.subtasks else None,
                    }
                )

        result = exporter.export_episodes(
            episode_indices=request.episodeIndices,
            edits_map=edits_map,
        )

        return ExportResultResponse(
            success=result.success,
            outputFiles=result.output_files,
            error=result.error,
            stats=result.stats,
        )

    except ImportError as e:
        raise HTTPException(
            status_code=501,
            detail=f"Export not available: {e}",
        )
    except HDF5ExportError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Export failed: {e}",
        )


@router.post("/{dataset_id}/export/stream")
async def export_episodes_stream(
    dataset_id: str,
    request: ExportRequest,
    service: DatasetService = Depends(get_dataset_service),
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
    dataset_path = service._get_dataset_path(dataset_id)
    if not dataset_path:
        raise HTTPException(
            status_code=400,
            detail=f"Dataset '{dataset_id}' does not have a local path for export",
        )

    # Validate output path
    output_path = Path(request.outputPath)
    try:
        output_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid output path: {e}",
        )

    async def event_generator():
        try:
            exporter = HDF5Exporter(dataset_path, output_path)

            # Parse edit operations
            edits_map: dict[int, EpisodeEditOperations] | None = None
            if request.applyEdits and request.edits:
                edits_map = {}
                for episode_idx, edit_req in request.edits.items():
                    # Convert string key to int (JSON keys are always strings)
                    idx = int(episode_idx) if isinstance(episode_idx, str) else episode_idx
                    edits_map[idx] = parse_edit_operations(
                        {
                            "datasetId": dataset_id,
                            "episodeIndex": edit_req.episodeIndex,
                            "globalTransform": edit_req.globalTransform.model_dump()
                            if edit_req.globalTransform
                            else None,
                            "cameraTransforms": {k: v.model_dump() for k, v in edit_req.cameraTransforms.items()}
                            if edit_req.cameraTransforms
                            else None,
                            "removedFrames": edit_req.removedFrames,
                            "insertedFrames": [i.model_dump() for i in edit_req.insertedFrames]
                            if edit_req.insertedFrames
                            else None,
                            "subtasks": [s.model_dump() for s in edit_req.subtasks] if edit_req.subtasks else None,
                        }
                    )

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
                lambda: exporter.export_episodes(
                    episode_indices=request.episodeIndices,
                    edits_map=edits_map,
                    progress_callback=progress_callback,
                ),
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
            complete_data = {
                "success": result.success,
                "outputFiles": result.output_files,
                "error": result.error,
                "stats": result.stats,
            }
            yield f"event: complete\ndata: {json.dumps(complete_data)}\n\n"

        except ImportError as e:
            error_msg = f"Export not available: {e}"
            yield f"event: error\ndata: {json.dumps({'error': error_msg})}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

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
    dataset_id: str,
    episode_indices: str = Query(..., description="Comma-separated episode indices"),
    removed_frames: str | None = Query(None, description="Comma-separated frame indices to remove"),
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

    # Parse episode indices
    try:
        indices = [int(i.strip()) for i in episode_indices.split(",")]
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid episode_indices format. Use comma-separated integers.",
        )

    # Parse removed frames
    removed: set[int] = set()
    if removed_frames:
        try:
            removed = {int(i.strip()) for i in removed_frames.split(",")}
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid removed_frames format. Use comma-separated integers.",
            )

    # Calculate preview stats
    total_original_frames = 0
    total_output_frames = 0

    for episode_idx in indices:
        episode = await service.get_episode(dataset_id, episode_idx)
        if episode:
            original = episode.meta.length
            output = original - len([f for f in removed if f < original])
            total_original_frames += original
            total_output_frames += output

    return {
        "episodeCount": len(indices),
        "originalFrames": total_original_frames,
        "removedFrames": total_original_frames - total_output_frames,
        "outputFrames": total_output_frames,
        "estimatedSizeMb": total_output_frames * 0.1,  # Rough estimate
    }
