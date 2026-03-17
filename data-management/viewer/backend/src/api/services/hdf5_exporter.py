"""
HDF5 Exporter service for episode data with edit operations applied.

Exports episodes to new HDF5 files with:
- Frame removal via boolean indexing
- Image transforms (crop/resize)
- Chunked gzip compression
- Progress callbacks for streaming updates
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from ..models.datasources import FrameInsertion
from .frame_interpolation import interpolate_frame_data, interpolate_image
from .hdf5_loader import HDF5Loader, HDF5LoaderError
from .image_transform import (
    CropRegion,
    ImageTransform,
    ImageTransformError,
    ResizeDimensions,
    apply_camera_transforms,
)

# h5py is an optional dependency
try:
    import h5py

    HDF5_AVAILABLE = True
except ImportError:
    HDF5_AVAILABLE = False


@dataclass
class SubtaskSegment:
    """A labeled segment of frames representing a sub-task."""

    id: str
    """Unique identifier."""
    label: str
    """Human-readable label."""
    frame_range: tuple[int, int]
    """Frame range [start, end] inclusive."""
    color: str
    """Display color (hex)."""
    source: str
    """How this segment was created: 'manual' or 'auto'."""
    description: str | None = None
    """Optional description."""


@dataclass
class EpisodeEditOperations:
    """Complete set of edit operations for an episode."""

    dataset_id: str
    """Dataset identifier."""
    episode_index: int
    """Episode index within the dataset."""
    global_transform: ImageTransform | None = None
    """Transform applied to all cameras."""
    camera_transforms: dict[str, ImageTransform] | None = None
    """Per-camera transform overrides."""
    removed_frames: set[int] | None = None
    """Frame indices to exclude from export."""
    inserted_frames: list[FrameInsertion] | None = None
    """Frame insertion specifications for interpolated frames."""
    subtasks: list[SubtaskSegment] | None = None
    """Sub-task segments for this episode."""


@dataclass
class ExportProgress:
    """Progress update during export."""

    current_episode: int
    """Current episode being processed."""
    total_episodes: int
    """Total episodes to process."""
    current_frame: int
    """Current frame being processed."""
    total_frames: int
    """Total frames in current episode."""
    percentage: float
    """Overall progress percentage (0-100)."""
    status: str
    """Current operation description."""


@dataclass
class ExportResult:
    """Result of an export operation."""

    success: bool
    """Whether export completed successfully."""
    output_files: list[str]
    """Output file paths."""
    error: str | None = None
    """Error message if failed."""
    stats: dict[str, Any] = field(default_factory=dict)
    """Export statistics."""


class HDF5ExportError(Exception):
    """Exception raised for HDF5 export failures."""

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


ProgressCallback = Callable[[ExportProgress], None]


class HDF5Exporter:
    """
    Exports episode data to HDF5 files with edit operations applied.

    Supports:
    - Frame removal (boolean indexing)
    - Image transforms (crop/resize)
    - Chunked gzip compression
    - Progress callbacks

    Example:
        >>> exporter = HDF5Exporter(
        ...     src_path="/data/dataset",
        ...     dst_path="/output"
        ... )
        >>> result = exporter.export_episode(
        ...     episode_index=0,
        ...     edits=EpisodeEditOperations(...),
        ...     progress_callback=print,
        ... )
    """

    def __init__(
        self,
        src_path: str | Path,
        dst_path: str | Path,
        compression: str = "gzip",
        compression_level: int = 4,
    ):
        """
        Initialize the exporter.

        Args:
            src_path: Source dataset directory.
            dst_path: Destination directory for exported files.
            compression: Compression algorithm ('gzip', 'lzf', or None).
            compression_level: Compression level (1-9 for gzip).

        Raises:
            ImportError: If h5py is not installed.
        """
        if not HDF5_AVAILABLE:
            raise ImportError("HDF5 export requires h5py package. Install with: pip install h5py")

        self.src_path = Path(src_path)
        self.dst_path = Path(dst_path)
        self.compression = compression
        self.compression_level = compression_level

        self.loader = HDF5Loader(self.src_path)

    def export_episode(
        self,
        episode_index: int,
        edits: EpisodeEditOperations | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> ExportResult:
        """
        Export a single episode with edit operations applied.

        Args:
            episode_index: Episode index to export.
            edits: Edit operations to apply (None = no edits).
            progress_callback: Optional callback for progress updates.

        Returns:
            ExportResult with success status and output files.
        """
        start_time = datetime.now()
        output_files: list[str] = []

        try:
            # Create output directory
            self.dst_path.mkdir(parents=True, exist_ok=True)

            # Load source episode with images
            if progress_callback:
                progress_callback(
                    ExportProgress(
                        current_episode=episode_index,
                        total_episodes=1,
                        current_frame=0,
                        total_frames=0,
                        percentage=0,
                        status="Loading source episode...",
                    )
                )

            episode = self.loader.load_episode(episode_index, load_images=True)

            # Determine valid frame indices
            total_frames = episode.length
            removed_frames = edits.removed_frames if edits else None
            valid_indices = self._get_valid_indices(total_frames, removed_frames)
            # Count insertions that will succeed (not at/after last valid frame)
            inserted_frames = edits.inserted_frames if edits else None
            insertion_count = 0
            if inserted_frames:
                for ins in inserted_frames:
                    if ins.after_frame_index in valid_indices:
                        pos = valid_indices.index(ins.after_frame_index)
                        if pos < len(valid_indices) - 1:
                            insertion_count += 1
            output_frames = len(valid_indices) + insertion_count

            if progress_callback:
                progress_callback(
                    ExportProgress(
                        current_episode=episode_index,
                        total_episodes=1,
                        current_frame=0,
                        total_frames=output_frames,
                        percentage=10,
                        status=f"Exporting {output_frames} frames (removed {total_frames - output_frames})...",
                    )
                )

            # Output file path
            output_path = self.dst_path / f"episode_{episode_index:06d}.hdf5"

            # Export to HDF5
            with h5py.File(output_path, "w") as dst:
                # Copy and filter trajectory data
                self._export_trajectory_data(
                    dst,
                    episode,
                    valid_indices,
                    edits.inserted_frames if edits else None,
                )

                # Apply transforms and export images
                if episode.images:
                    self._export_images(
                        dst,
                        episode.images,
                        valid_indices,
                        edits.global_transform if edits else None,
                        edits.camera_transforms if edits else None,
                        progress_callback,
                        episode_index,
                        output_frames,
                        edits.inserted_frames if edits else None,
                    )

                # Copy metadata
                self._export_metadata(dst, episode, edits)

            output_files.append(str(output_path))

            # Export metadata JSON
            meta_path = self.dst_path / f"episode_{episode_index:06d}.meta.json"
            self._export_meta_json(meta_path, episode_index, edits, total_frames, output_frames)
            output_files.append(str(meta_path))

            # Export subtasks if present
            if edits and edits.subtasks:
                subtasks_path = self.dst_path / f"episode_{episode_index:06d}.subtasks.json"
                self._export_subtasks(subtasks_path, edits.subtasks, valid_indices)
                output_files.append(str(subtasks_path))

            if progress_callback:
                progress_callback(
                    ExportProgress(
                        current_episode=episode_index,
                        total_episodes=1,
                        current_frame=output_frames,
                        total_frames=output_frames,
                        percentage=100,
                        status="Export complete",
                    )
                )

            duration_ms = (datetime.now() - start_time).total_seconds() * 1000

            return ExportResult(
                success=True,
                output_files=output_files,
                stats={
                    "total_episodes": 1,
                    "total_frames": output_frames,
                    "removed_frames": total_frames - output_frames,
                    "duration_ms": duration_ms,
                },
            )

        except (HDF5LoaderError, HDF5ExportError, ImageTransformError) as e:
            return ExportResult(
                success=False,
                output_files=output_files,
                error=str(e),
            )
        except Exception as e:
            return ExportResult(
                success=False,
                output_files=output_files,
                error=f"Unexpected error: {e}",
            )

    def export_episodes(
        self,
        episode_indices: list[int],
        edits_map: dict[int, EpisodeEditOperations] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> ExportResult:
        """
        Export multiple episodes.

        Args:
            episode_indices: List of episode indices to export.
            edits_map: Dict of episode_index -> EpisodeEditOperations.
            progress_callback: Optional callback for progress updates.

        Returns:
            ExportResult with combined stats.
        """
        start_time = datetime.now()
        edits_map = edits_map or {}
        all_output_files: list[str] = []
        total_frames = 0
        removed_frames = 0
        errors: list[str] = []

        for i, episode_index in enumerate(episode_indices):
            edits = edits_map.get(episode_index)

            def make_episode_progress(idx: int, ep_idx: int) -> Callable[[ExportProgress], None]:
                def episode_progress(p: ExportProgress) -> None:
                    if progress_callback:
                        # Adjust percentage for multi-episode export
                        base_pct = (idx / len(episode_indices)) * 100
                        episode_pct = p.percentage / len(episode_indices)
                        progress_callback(
                            ExportProgress(
                                current_episode=ep_idx,
                                total_episodes=len(episode_indices),
                                current_frame=p.current_frame,
                                total_frames=p.total_frames,
                                percentage=base_pct + episode_pct,
                                status=p.status,
                            )
                        )

                return episode_progress

            progress_fn = make_episode_progress(i, episode_index)
            result = self.export_episode(episode_index, edits, progress_fn)

            all_output_files.extend(result.output_files)
            total_frames += result.stats.get("total_frames", 0)
            removed_frames += result.stats.get("removed_frames", 0)

            if not result.success and result.error:
                errors.append(f"Episode {episode_index}: {result.error}")

        duration_ms = (datetime.now() - start_time).total_seconds() * 1000

        return ExportResult(
            success=len(errors) == 0,
            output_files=all_output_files,
            error="\n".join(errors) if errors else None,
            stats={
                "total_episodes": len(episode_indices),
                "total_frames": total_frames,
                "removed_frames": removed_frames,
                "duration_ms": duration_ms,
            },
        )

    def _get_valid_indices(
        self,
        total_frames: int,
        removed_frames: set[int] | None,
    ) -> list[int]:
        """Get list of valid frame indices after removal."""
        if not removed_frames:
            return list(range(total_frames))
        return [i for i in range(total_frames) if i not in removed_frames]

    def _compute_insertions(
        self,
        data: NDArray,
        insertions: list[FrameInsertion],
        valid_indices: list[int],
    ) -> list[tuple[int, NDArray]]:
        """Compute interpolated rows for frame insertions.

        Args:
            data: Original array data with shape (N, ...).
            insertions: List of FrameInsertion specifications.
            valid_indices: Indices remaining after removal filtering.

        Returns:
            List of (insert_position, interpolated_row) tuples.
        """
        results = []

        for insertion in insertions:
            after_idx = insertion.after_frame_index
            t = insertion.interpolation_factor

            # Find position in valid_indices
            try:
                pos = valid_indices.index(after_idx)
            except ValueError:
                # Insertion point was removed, skip
                continue

            if pos >= len(valid_indices) - 1:
                # Cannot insert after last frame
                continue

            # Compute interpolation using original data
            interpolated = interpolate_frame_data(data, after_idx, t)

            results.append((pos, interpolated))

        return results

    def _apply_insertions(
        self,
        data: NDArray,
        insertions: list[tuple[int, NDArray]],
    ) -> NDArray:
        """Apply computed insertions to array data.

        Args:
            data: Array with shape (N, ...).
            insertions: List of (insert_after_position, row) tuples.

        Returns:
            New array with inserted rows.
        """
        if not insertions:
            return data

        # Sort by position descending to avoid index shifting
        sorted_insertions = sorted(insertions, key=lambda x: x[0], reverse=True)

        result = data
        for after_pos, new_row in sorted_insertions:
            # Insert after the position (so at index after_pos + 1)
            result = np.insert(result, after_pos + 1, new_row, axis=0)

        return result

    def _export_trajectory_data(
        self,
        dst: "h5py.File",
        episode: Any,
        valid_indices: list[int],
        inserted_frames: list[FrameInsertion] | None = None,
    ) -> None:
        """Export trajectory data with frame filtering and insertions."""
        data_group = dst.create_group("data")

        # Helper to apply insertions to a data array
        def process_data(data: NDArray) -> NDArray:
            filtered = data[valid_indices]
            if inserted_frames:
                insertions = self._compute_insertions(data, inserted_frames, valid_indices)
                filtered = self._apply_insertions(filtered, insertions)
            return filtered

        # Joint positions
        data_group.create_dataset(
            "qpos",
            data=process_data(episode.joint_positions),
            compression=self.compression,
            compression_opts=self.compression_level if self.compression == "gzip" else None,
        )

        # Joint velocities
        if episode.joint_velocities is not None:
            data_group.create_dataset(
                "qvel",
                data=process_data(episode.joint_velocities),
                compression=self.compression,
                compression_opts=self.compression_level if self.compression == "gzip" else None,
            )

        # Timestamps
        data_group.create_dataset(
            "timestamps",
            data=process_data(episode.timestamps),
            compression=self.compression,
            compression_opts=self.compression_level if self.compression == "gzip" else None,
        )

        # Actions
        if episode.actions is not None:
            data_group.create_dataset(
                "action",
                data=process_data(episode.actions),
                compression=self.compression,
                compression_opts=self.compression_level if self.compression == "gzip" else None,
            )

        # End-effector pose
        if episode.end_effector_pose is not None:
            data_group.create_dataset(
                "ee_pose",
                data=process_data(episode.end_effector_pose),
                compression=self.compression,
                compression_opts=self.compression_level if self.compression == "gzip" else None,
            )

        # Gripper states
        if episode.gripper_states is not None:
            data_group.create_dataset(
                "gripper",
                data=process_data(episode.gripper_states),
                compression=self.compression,
                compression_opts=self.compression_level if self.compression == "gzip" else None,
            )

    def _export_images(
        self,
        dst: "h5py.File",
        images: dict[str, NDArray[np.uint8]],
        valid_indices: list[int],
        global_transform: ImageTransform | None,
        camera_transforms: dict[str, ImageTransform] | None,
        progress_callback: ProgressCallback | None,
        episode_index: int,
        total_frames: int,
        inserted_frames: list[FrameInsertion] | None = None,
    ) -> None:
        """Export images with transforms, frame filtering, and insertions."""
        # Filter frames first
        filtered_images = {camera: frames[valid_indices] for camera, frames in images.items()}

        # Apply insertions before transforms
        if inserted_frames:
            for camera, _original_frames in images.items():
                filtered = filtered_images[camera]
                img_insertions: list[tuple[int, NDArray]] = []
                for insertion in inserted_frames:
                    after_idx = insertion.after_frame_index
                    try:
                        pos = valid_indices.index(after_idx)
                    except ValueError:
                        continue
                    if pos >= len(valid_indices) - 1:
                        continue
                    # Interpolate using filtered (post-removal) images
                    interpolated_img = interpolate_image(
                        filtered[pos],
                        filtered[pos + 1],
                        insertion.interpolation_factor,
                    )
                    img_insertions.append((pos, interpolated_img))
                filtered_images[camera] = self._apply_insertions(filtered, img_insertions)

        # Apply transforms
        if global_transform or camera_transforms:
            frame_count = [0]

            def transform_progress(camera: str, current: int, total: int) -> None:
                if progress_callback:
                    frame_count[0] += 1
                    pct = 10 + (frame_count[0] / (total_frames * len(filtered_images))) * 80
                    progress_callback(
                        ExportProgress(
                            current_episode=episode_index,
                            total_episodes=1,
                            current_frame=current,
                            total_frames=total,
                            percentage=pct,
                            status=f"Transforming {camera}...",
                        )
                    )

            filtered_images = apply_camera_transforms(
                filtered_images,
                global_transform,
                camera_transforms,
                transform_progress,
            )

        # Create observations group
        obs_group = dst.create_group("observations")
        img_group = obs_group.create_group("images")

        # Write each camera
        for camera, frames in filtered_images.items():
            # Chunked by single frame for efficient streaming reads
            h, w, c = frames.shape[1:]
            chunks = (1, h, w, c)

            img_group.create_dataset(
                camera,
                data=frames,
                chunks=chunks,
                compression=self.compression,
                compression_opts=self.compression_level if self.compression == "gzip" else None,
            )

    def _export_metadata(
        self,
        dst: "h5py.File",
        episode: Any,
        edits: EpisodeEditOperations | None,
    ) -> None:
        """Export metadata attributes."""
        # Copy original metadata
        for key, value in episode.metadata.items():
            if isinstance(value, list | dict):
                dst.attrs[key] = json.dumps(value)
            else:
                dst.attrs[key] = value

        # Add export metadata
        dst.attrs["episode_index"] = episode.episode_index
        dst.attrs["task_index"] = episode.task_index
        dst.attrs["export_timestamp"] = datetime.now().isoformat()
        dst.attrs["edits_applied"] = edits is not None

    def _export_meta_json(
        self,
        path: Path,
        episode_index: int,
        edits: EpisodeEditOperations | None,
        original_frames: int,
        output_frames: int,
    ) -> None:
        """Export metadata JSON file with edit history."""
        meta = {
            "episode_index": episode_index,
            "export_timestamp": datetime.now().isoformat(),
            "original_frames": original_frames,
            "output_frames": output_frames,
            "edits_applied": edits is not None,
        }

        if edits:
            edit_info: dict[str, Any] = {}

            if edits.global_transform:
                edit_info["global_transform"] = {
                    "crop": {
                        "x": edits.global_transform.crop.x,
                        "y": edits.global_transform.crop.y,
                        "width": edits.global_transform.crop.width,
                        "height": edits.global_transform.crop.height,
                    }
                    if edits.global_transform.crop
                    else None,
                    "resize": {
                        "width": edits.global_transform.resize.width,
                        "height": edits.global_transform.resize.height,
                    }
                    if edits.global_transform.resize
                    else None,
                }

            if edits.removed_frames:
                edit_info["removed_frames"] = sorted(edits.removed_frames)

            if edits.inserted_frames:
                edit_info["inserted_frames"] = [
                    {
                        "after_frame_index": ins.after_frame_index,
                        "interpolation_factor": ins.interpolation_factor,
                    }
                    for ins in edits.inserted_frames
                ]

            if edits.subtasks:
                edit_info["subtasks_count"] = len(edits.subtasks)

            meta["edits"] = edit_info

        path.write_text(json.dumps(meta, indent=2))

    def _export_subtasks(
        self,
        path: Path,
        subtasks: list[SubtaskSegment],
        valid_indices: list[int],
    ) -> None:
        """Export subtask segments with adjusted frame indices."""
        # Create index mapping from original to new frame indices
        index_map = {orig: new for new, orig in enumerate(valid_indices)}

        adjusted_subtasks = []
        for st in subtasks:
            # Adjust frame range to new indices
            new_start = index_map.get(st.frame_range[0])
            new_end = index_map.get(st.frame_range[1])

            # Skip if segment is entirely removed
            if new_start is None or new_end is None:
                continue

            adjusted_subtasks.append(
                {
                    "id": st.id,
                    "label": st.label,
                    "frame_range": [new_start, new_end],
                    "color": st.color,
                    "source": st.source,
                    "description": st.description,
                }
            )

        path.write_text(json.dumps(adjusted_subtasks, indent=2))


def parse_edit_operations(data: dict) -> EpisodeEditOperations:
    """
    Parse edit operations from API request data.

    Args:
        data: Dict with edit operation fields.

    Returns:
        EpisodeEditOperations instance.
    """
    global_transform = None
    if data.get("globalTransform"):
        gt = data["globalTransform"]
        global_transform = ImageTransform(
            crop=CropRegion(**gt["crop"]) if gt.get("crop") else None,
            resize=ResizeDimensions(**gt["resize"]) if gt.get("resize") else None,
        )

    camera_transforms = None
    if data.get("cameraTransforms"):
        camera_transforms = {}
        for camera, ct in data["cameraTransforms"].items():
            camera_transforms[camera] = ImageTransform(
                crop=CropRegion(**ct["crop"]) if ct.get("crop") else None,
                resize=ResizeDimensions(**ct["resize"]) if ct.get("resize") else None,
            )

    removed_frames = None
    if data.get("removedFrames"):
        removed_frames = set(data["removedFrames"])

    inserted_frames = None
    if data.get("insertedFrames"):
        inserted_frames = [
            FrameInsertion(
                after_frame_index=ins["afterFrameIndex"],
                interpolation_factor=ins.get("interpolationFactor", 0.5),
            )
            for ins in data["insertedFrames"]
        ]

    subtasks = None
    if data.get("subtasks"):
        subtasks = [
            SubtaskSegment(
                id=st["id"],
                label=st["label"],
                frame_range=tuple(st["frameRange"]),
                color=st["color"],
                source=st["source"],
                description=st.get("description"),
            )
            for st in data["subtasks"]
        ]

    return EpisodeEditOperations(
        dataset_id=data.get("datasetId", ""),
        episode_index=data.get("episodeIndex", 0),
        global_transform=global_transform,
        camera_transforms=camera_transforms,
        removed_frames=removed_frames,
        inserted_frames=inserted_frames,
        subtasks=subtasks,
    )
