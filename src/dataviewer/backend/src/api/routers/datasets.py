"""
Dataset API endpoints for LeRobot annotation system.

Provides endpoints for listing datasets, retrieving metadata,
and accessing episode information with HDF5 and LeRobot parquet support.
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from ..models.datasources import DatasetInfo, EpisodeData, EpisodeMeta, TrajectoryPoint
from ..services.dataset_service import DatasetService, get_dataset_service
from ..validation import validated_camera_name, validated_dataset_id

router = APIRouter()


class DatasetCapabilities(BaseModel):
    """Capabilities available for a dataset."""

    hdf5_support: bool
    """Whether h5py is installed and available."""

    has_hdf5_files: bool
    """Whether this dataset has HDF5 episode files."""

    lerobot_support: bool
    """Whether pyarrow is installed and available."""

    is_lerobot_dataset: bool
    """Whether this dataset is in LeRobot parquet format."""

    episode_count: int
    """Number of episodes detected."""


@router.get("", response_model=list[DatasetInfo])
async def list_datasets(
    service: DatasetService = Depends(get_dataset_service),
) -> list[DatasetInfo]:
    """
    List all available datasets.

    Returns metadata for all configured datasets including episode counts,
    FPS, features, and available tasks.
    """
    return await service.list_datasets()


@router.get("/{dataset_id}", response_model=DatasetInfo)
async def get_dataset(
    dataset_id: str = Depends(validated_dataset_id),
    service: DatasetService = Depends(get_dataset_service),
) -> DatasetInfo:
    """
    Get metadata for a specific dataset.

    Returns the dataset's info.json content including features,
    tasks, and episode count.
    """
    dataset = await service.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")
    return dataset


@router.get("/{dataset_id}/capabilities", response_model=DatasetCapabilities)
async def get_dataset_capabilities(
    dataset_id: str = Depends(validated_dataset_id),
    service: DatasetService = Depends(get_dataset_service),
) -> DatasetCapabilities:
    """
    Get capabilities and format support status for a dataset.

    Returns information about whether the dataset supports HDF5 or LeRobot loading
    and how many episodes are available.
    """
    dataset = await service.get_dataset(dataset_id)
    episode_count = dataset.total_episodes if dataset else 0

    # Check format support
    has_hdf5 = service.dataset_has_hdf5(dataset_id)
    is_lerobot = service.dataset_is_lerobot(dataset_id)

    return DatasetCapabilities(
        hdf5_support=service.has_hdf5_support(),
        has_hdf5_files=has_hdf5,
        lerobot_support=service.has_lerobot_support(),
        is_lerobot_dataset=is_lerobot,
        episode_count=episode_count,
    )


@router.get("/{dataset_id}/episodes", response_model=list[EpisodeMeta])
async def list_episodes(
    dataset_id: str = Depends(validated_dataset_id),
    offset: int = Query(0, ge=0, description="Number of episodes to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum episodes to return"),
    has_annotations: bool | None = Query(None, description="Filter by annotation status"),
    task_index: int | None = Query(None, ge=0, description="Filter by task index"),
    service: DatasetService = Depends(get_dataset_service),
) -> list[EpisodeMeta]:
    """
    List episodes for a dataset with optional filtering.

    Returns episode metadata including index, length, task assignment,
    and annotation status. When HDF5 files are available, episode
    length and task index are loaded from the files.
    """
    dataset = await service.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")
    return await service.list_episodes(
        dataset_id,
        offset=offset,
        limit=limit,
        has_annotations=has_annotations,
        task_index=task_index,
    )


@router.get("/{dataset_id}/episodes/{episode_idx}", response_model=EpisodeData)
async def get_episode(
    episode_idx: int,
    response: Response,
    dataset_id: str = Depends(validated_dataset_id),
    service: DatasetService = Depends(get_dataset_service),
) -> EpisodeData:
    """
    Get complete data for a specific episode.

    Returns episode metadata, video URLs for each camera,
    and trajectory data points. When HDF5 files are available,
    trajectory data is loaded directly from the HDF5 file.
    """
    dataset = await service.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")
    episode = await service.get_episode(dataset_id, episode_idx)
    if episode is None:
        raise HTTPException(
            status_code=404,
            detail=f"Episode {episode_idx} not found in dataset '{dataset_id}'",
        )
    response.headers["Cache-Control"] = "private, max-age=60"
    return episode


@router.get(
    "/{dataset_id}/episodes/{episode_idx}/trajectory",
    response_model=list[TrajectoryPoint],
)
async def get_episode_trajectory(
    episode_idx: int,
    response: Response,
    dataset_id: str = Depends(validated_dataset_id),
    service: DatasetService = Depends(get_dataset_service),
) -> list[TrajectoryPoint]:
    """
    Get only trajectory data for an episode.

    Optimized endpoint for loading trajectory data without full episode
    metadata. Useful for analysis operations.
    """
    trajectory = await service.get_episode_trajectory(dataset_id, episode_idx)
    if not trajectory:
        raise HTTPException(
            status_code=404,
            detail=f"No trajectory data for episode {episode_idx} in dataset '{dataset_id}'",
        )
    response.headers["Cache-Control"] = "private, max-age=60"
    return trajectory


@router.get("/{dataset_id}/episodes/{episode_idx}/frames/{frame_idx}")
async def get_episode_frame(
    episode_idx: int,
    frame_idx: int,
    dataset_id: str = Depends(validated_dataset_id),
    camera: str = Query("il-camera", description="Camera name"),
    service: DatasetService = Depends(get_dataset_service),
) -> Response:
    """
    Get a single frame image from an episode.

    Returns the image as JPEG for the specified frame and camera.
    """
    try:
        image_bytes = await service.get_frame_image(dataset_id, episode_idx, frame_idx, camera)
        if image_bytes is None:
            raise HTTPException(
                status_code=404,
                detail=f"Frame {frame_idx} not found for camera '{camera}'",
            )
        return Response(
            content=image_bytes,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load frame: {e!s}",
        )


@router.get("/{dataset_id}/episodes/{episode_idx}/cameras")
async def get_episode_cameras(
    episode_idx: int,
    dataset_id: str = Depends(validated_dataset_id),
    service: DatasetService = Depends(get_dataset_service),
) -> list[str]:
    """
    Get list of available cameras for an episode.
    """
    cameras = await service.get_episode_cameras(dataset_id, episode_idx)
    return cameras


@router.get(
    "/{dataset_id}/episodes/{episode_idx}/video/{camera}",
    response_model=None,
)
@router.head(
    "/{dataset_id}/episodes/{episode_idx}/video/{camera}",
    response_model=None,
    include_in_schema=False,
)
async def get_episode_video(
    request: Request,
    episode_idx: int,
    dataset_id: str = Depends(validated_dataset_id),
    camera: str = Depends(validated_camera_name),
    service: DatasetService = Depends(get_dataset_service),
) -> FileResponse | StreamingResponse | Response:
    """
    Get video file for an episode and camera.

    Returns the video file for streaming with HTTP Range support for
    seeking. Supports LeRobot parquet datasets with video files stored
    alongside the parquet data, as well as datasets stored in Azure
    Blob Storage.

    Note: camera parameter can include dots (e.g., 'observation.images.color')
    """
    video_path = await asyncio.to_thread(service.get_video_file_path, dataset_id, episode_idx, camera)

    if video_path is not None:
        if not service.is_safe_video_path(video_path):
            raise HTTPException(status_code=400, detail="Path traversal detected: resolved path escapes base directory")
        video_file = Path(video_path)
        if not video_file.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Video file not found: {video_path}",
            )
        suffix = video_file.suffix.lower()
        media_types = {
            ".mp4": "video/mp4",
            ".webm": "video/webm",
            ".avi": "video/x-msvideo",
            ".mov": "video/quicktime",
        }
        media_type = media_types.get(suffix, "video/mp4")
        service.schedule_video_prefetch(dataset_id, episode_idx, camera)
        return FileResponse(
            path=str(video_file),
            media_type=media_type,
            filename=f"{dataset_id}_ep{episode_idx}_{camera.replace('.', '_')}{suffix}",
            headers={"Cache-Control": "public, max-age=86400, immutable"},
        )

    # Fall back to blob streaming when local file is unavailable
    if service.has_blob_provider():
        blob_path = await service.get_blob_video_path(dataset_id, episode_idx, camera)
        if blob_path is None:
            raise HTTPException(
                status_code=404,
                detail=f"Video not found in blob storage for episode {episode_idx}, camera '{camera}'",
            )

        offset, length = _parse_range_header(request.headers.get("range"))
        stream_result = await service.get_blob_video_stream(blob_path, offset=offset, length=length)
        if stream_result is not None:
            headers, media_type, stream = stream_result
            status_code = 206 if offset is not None else 200

            if request.method == "HEAD":
                return Response(
                    status_code=status_code,
                    headers=headers,
                    media_type=media_type,
                )

            return StreamingResponse(
                stream,
                status_code=status_code,
                media_type=media_type,
                headers=headers,
            )

    raise HTTPException(
        status_code=404,
        detail=f"Video not found for episode {episode_idx}, camera '{camera}'",
    )


def _parse_range_header(range_header: str | None) -> tuple[int | None, int | None]:
    """Parse an HTTP Range header into (offset, length). Supports 'bytes=START-END' and 'bytes=START-'."""
    if not range_header or not range_header.startswith("bytes="):
        return None, None

    range_spec = range_header[6:]
    parts = range_spec.split("-", 1)
    if not parts[0]:
        return None, None

    start = int(parts[0])
    if parts[1]:
        end = int(parts[1])
        return start, end - start + 1
    return start, None


class EpisodeCacheStats(BaseModel):
    """Cache performance metrics."""

    capacity: int
    size: int
    hits: int
    misses: int
    hit_rate: float
    total_bytes: int
    max_memory_bytes: int


@router.get("/cache/stats", response_model=EpisodeCacheStats)
async def get_cache_stats(
    service: DatasetService = Depends(get_dataset_service),
) -> EpisodeCacheStats:
    """Return episode cache performance metrics."""
    stats = service._episode_cache.stats()
    return EpisodeCacheStats(
        capacity=stats.capacity,
        size=stats.size,
        hits=stats.hits,
        misses=stats.misses,
        hit_rate=stats.hit_rate,
        total_bytes=stats.total_bytes,
        max_memory_bytes=stats.max_memory_bytes,
    )


@router.post("/{dataset_id}/cache/warm")
async def warm_cache(
    count: int = Query(5, ge=1, le=20, description="Number of episodes to preload"),
    dataset_id: str = Depends(validated_dataset_id),
    service: DatasetService = Depends(get_dataset_service),
) -> dict:
    """
    Preload the first N episodes into the LRU cache.

    Designed to be called on dataset selection so the initial episode
    loads are instant. Cancels any in-progress video generation for
    the previously warmed dataset before proceeding.
    """
    previous_dataset = getattr(service, "_warm_cached_dataset_id", None)
    if previous_dataset and previous_dataset != dataset_id:
        await asyncio.to_thread(service.cancel_video_generation, previous_dataset)

    service._warm_cached_dataset_id = dataset_id  # type: ignore[attr-defined]

    dataset = await service.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")

    loaded = 0
    total = min(count, dataset.total_episodes)
    for idx in range(total):
        episode = await service.get_episode(dataset_id, idx)
        if episode is not None:
            loaded += 1

    queued = service.schedule_bulk_video_generation(dataset_id)

    return {"dataset_id": dataset_id, "loaded": loaded, "requested": total, "videos_queued": queued}
