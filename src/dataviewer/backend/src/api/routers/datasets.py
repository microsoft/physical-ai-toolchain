"""
Dataset API endpoints for LeRobot annotation system.

Provides endpoints for listing datasets, retrieving metadata,
and accessing episode information with HDF5 and LeRobot parquet support.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from ..models.datasources import DatasetInfo, EpisodeData, EpisodeMeta, TrajectoryPoint
from ..services.dataset_service import DatasetService, get_dataset_service

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
    dataset_id: str,
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
    dataset_id: str,
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

    if has_hdf5:
        # Get episode count from HDF5 loader
        hdf5_loader = service._get_hdf5_loader(dataset_id)
        if hdf5_loader:
            try:
                episodes = hdf5_loader.list_episodes()
                episode_count = max(episode_count, len(episodes))
            except Exception:
                pass  # Best-effort; loader may not support listing
    elif is_lerobot:
        # Get episode count from LeRobot loader
        lerobot_loader = service._get_lerobot_loader(dataset_id)
        if lerobot_loader:
            try:
                episodes = lerobot_loader.list_episodes()
                episode_count = max(episode_count, len(episodes))
            except Exception:
                pass  # Best-effort; loader may not support listing

    return DatasetCapabilities(
        hdf5_support=service.has_hdf5_support(),
        has_hdf5_files=has_hdf5,
        lerobot_support=service.has_lerobot_support(),
        is_lerobot_dataset=is_lerobot,
        episode_count=episode_count,
    )


@router.get("/{dataset_id}/episodes", response_model=list[EpisodeMeta])
async def list_episodes(
    dataset_id: str,
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
    return await service.list_episodes(
        dataset_id,
        offset=offset,
        limit=limit,
        has_annotations=has_annotations,
        task_index=task_index,
    )


@router.get("/{dataset_id}/episodes/{episode_idx}", response_model=EpisodeData)
async def get_episode(
    dataset_id: str,
    episode_idx: int,
    service: DatasetService = Depends(get_dataset_service),
) -> EpisodeData:
    """
    Get complete data for a specific episode.

    Returns episode metadata, video URLs for each camera,
    and trajectory data points. When HDF5 files are available,
    trajectory data is loaded directly from the HDF5 file.
    """
    episode = await service.get_episode(dataset_id, episode_idx)
    if episode is None:
        raise HTTPException(
            status_code=404,
            detail=f"Episode {episode_idx} not found in dataset '{dataset_id}'",
        )
    return episode


@router.get(
    "/{dataset_id}/episodes/{episode_idx}/trajectory",
    response_model=list[TrajectoryPoint],
)
async def get_episode_trajectory(
    dataset_id: str,
    episode_idx: int,
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
    return trajectory


@router.get("/{dataset_id}/episodes/{episode_idx}/frames/{frame_idx}")
async def get_episode_frame(
    dataset_id: str,
    episode_idx: int,
    frame_idx: int,
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
        return Response(content=image_bytes, media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load frame: {e!s}",
        )


@router.get("/{dataset_id}/episodes/{episode_idx}/cameras")
async def get_episode_cameras(
    dataset_id: str,
    episode_idx: int,
    service: DatasetService = Depends(get_dataset_service),
) -> list[str]:
    """
    Get list of available cameras for an episode.
    """
    cameras = await service.get_episode_cameras(dataset_id, episode_idx)
    return cameras


@router.get("/{dataset_id}/episodes/{episode_idx}/video/{camera:path}")
async def get_episode_video(
    dataset_id: str,
    episode_idx: int,
    camera: str,
    service: DatasetService = Depends(get_dataset_service),
) -> FileResponse:
    """
    Get video file for an episode and camera.

    Returns the video file for streaming. Supports LeRobot parquet datasets
    with video files stored alongside the parquet data.

    Note: camera parameter can include dots (e.g., 'observation.images.color')
    """
    video_path = service.get_video_file_path(dataset_id, episode_idx, camera)

    if video_path is None:
        raise HTTPException(
            status_code=404,
            detail=f"Video not found for episode {episode_idx}, camera '{camera}'",
        )

    video_file = Path(video_path)
    if not video_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Video file not found: {video_path}",
        )

    # Determine media type based on file extension
    suffix = video_file.suffix.lower()
    media_types = {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".avi": "video/x-msvideo",
        ".mov": "video/quicktime",
    }
    media_type = media_types.get(suffix, "video/mp4")

    return FileResponse(
        path=video_path,
        media_type=media_type,
        filename=f"{dataset_id}_ep{episode_idx}_{camera.replace('.', '_')}{suffix}",
    )
