"""
Aggregate analysis service for nested HDF5 episode datasets.

Computes dataset-level statistics, joint state occupancy/visitation maps,
and object detection summaries by walking session directories and loading
all episodes in bulk.
"""

import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from ..models.aggregate_analysis import (
    AggregateAnalysisResult,
    DatasetStatistics,
    DetectedObjectClass,
    DetectionSample,
    DetectionSummary,
    JointOccupancyMap,
    SessionStatistics,
    TemporalVisitationMap,
)

logger = logging.getLogger(__name__)

try:
    import h5py

    HDF5_AVAILABLE = True
except ImportError:
    HDF5_AVAILABLE = False
    h5py = None

# Default joint labels for the Hexagon bimanual task-space observation vector
_DEFAULT_JOINT_LABELS_16 = [
    "right_x",
    "right_y",
    "right_z",
    "right_qx",
    "right_qy",
    "right_qz",
    "right_qw",
    "right_gripper",
    "left_x",
    "left_y",
    "left_z",
    "left_qx",
    "left_qy",
    "left_qz",
    "left_qw",
    "left_gripper",
]


@dataclass
class EpisodeRecord:
    """Lightweight container for one episode's data arrays."""

    session_id: str
    episode_index: int
    qpos: NDArray[np.float32]
    action: NDArray[np.float32]
    num_frames: int
    has_images: bool = False


@dataclass
class DatasetBundle:
    """All episodes loaded from a nested HDF5 dataset."""

    episodes: list[EpisodeRecord] = field(default_factory=list)
    observation_dim: int = 0
    action_dim: int = 0
    fps: float = 30.0
    joint_labels: list[str] = field(default_factory=list)


class AggregateAnalyzer:
    """
    Analyzes a complete nested HDF5 dataset.

    Walks session directories, loads all episode HDF5 files, and computes
    aggregate statistics, joint occupancy maps, and temporal visitation maps.
    """

    def __init__(
        self,
        n_occupancy_bins: int = 50,
        n_temporal_bins: int = 30,
        n_detection_samples: int = 20,
        detection_confidence: float = 0.25,
    ) -> None:
        self.n_occupancy_bins = n_occupancy_bins
        self.n_temporal_bins = n_temporal_bins
        self.n_detection_samples = n_detection_samples
        self.detection_confidence = detection_confidence

    # ------------------------------------------------------------------
    # Dataset discovery and loading
    # ------------------------------------------------------------------

    @staticmethod
    def discover_sessions(dataset_path: Path) -> list[tuple[str, Path]]:
        """Find session directories containing HDF5 episode files.

        Returns list of (session_id, session_path) sorted by session_id.
        """
        sessions: list[tuple[str, Path]] = []
        if not dataset_path.is_dir():
            return sessions

        # Direct HDF5 files in root → treat root as a single session
        if any(dataset_path.glob("*.hdf5")):
            sessions.append(("", dataset_path))
            return sessions

        # Nested session directories
        for child in sorted(dataset_path.iterdir()):
            if child.is_dir() and any(child.glob("*.hdf5")):
                sessions.append((child.name, child))

        return sessions

    @staticmethod
    def _load_dataset_config(session_path: Path) -> dict:
        """Load dataset_config.json from a session directory."""
        config_path = session_path / "dataset_config.json"
        if config_path.exists():
            with open(config_path) as f:
                return json.load(f)
        return {}

    def load_dataset(self, dataset_path: Path) -> DatasetBundle:
        """Load all episodes from a nested HDF5 dataset."""
        if not HDF5_AVAILABLE:
            raise ImportError("h5py is required for HDF5 dataset analysis. Install with: pip install h5py")

        bundle = DatasetBundle()
        sessions = self.discover_sessions(dataset_path)

        for session_id, session_path in sessions:
            config = self._load_dataset_config(session_path)
            if config:
                bundle.observation_dim = config.get("observation_dimension", bundle.observation_dim)
                bundle.action_dim = config.get("action_dimension", bundle.action_dim)

            hdf5_files = sorted(session_path.glob("*.hdf5"), key=lambda p: _episode_sort_key(p))
            for ep_idx, hdf5_path in enumerate(hdf5_files):
                try:
                    record = self._load_episode(session_id, ep_idx, hdf5_path)
                    bundle.episodes.append(record)
                    if bundle.observation_dim == 0 and record.qpos.shape[1] > 0:
                        bundle.observation_dim = record.qpos.shape[1]
                    if bundle.action_dim == 0 and record.action.shape[1] > 0:
                        bundle.action_dim = record.action.shape[1]
                except Exception:
                    logger.warning("Failed to load %s", hdf5_path, exc_info=True)

        # Assign joint labels
        if bundle.observation_dim == 16:
            bundle.joint_labels = list(_DEFAULT_JOINT_LABELS_16)
        else:
            bundle.joint_labels = [f"joint_{i}" for i in range(bundle.observation_dim)]

        return bundle

    @staticmethod
    def _load_episode(session_id: str, ep_idx: int, hdf5_path: Path) -> EpisodeRecord:
        """Load qpos and action arrays from a single HDF5 file."""
        with h5py.File(hdf5_path, "r") as f:
            qpos = np.array(f["observations/qpos"], dtype=np.float32)
            action = np.array(f["action"], dtype=np.float32)
            has_images = "observations/images" in f
        return EpisodeRecord(
            session_id=session_id,
            episode_index=ep_idx,
            qpos=qpos,
            action=action,
            num_frames=qpos.shape[0],
            has_images=has_images,
        )

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def compute_statistics(self, dataset_id: str, bundle: DatasetBundle) -> DatasetStatistics:
        """Compute aggregate and per-session statistics."""
        lengths = np.array([ep.num_frames for ep in bundle.episodes], dtype=np.int64)
        if len(lengths) == 0:
            return DatasetStatistics(
                dataset_id=dataset_id,
                episode_count=0,
                total_frames=0,
                avg_episode_length=0.0,
                min_episode_length=0,
                max_episode_length=0,
                std_episode_length=0.0,
                fps=bundle.fps,
                observation_dim=bundle.observation_dim,
                action_dim=bundle.action_dim,
                session_count=0,
            )

        # Per-session breakdown
        session_map: dict[str, list[int]] = {}
        for ep in bundle.episodes:
            session_map.setdefault(ep.session_id, []).append(ep.num_frames)

        sessions = []
        for sid, ep_lengths in sorted(session_map.items()):
            arr = np.array(ep_lengths)
            sessions.append(
                SessionStatistics(
                    session_id=sid,
                    episode_count=len(arr),
                    total_frames=int(arr.sum()),
                    avg_episode_length=float(arr.mean()),
                    min_episode_length=int(arr.min()),
                    max_episode_length=int(arr.max()),
                )
            )

        return DatasetStatistics(
            dataset_id=dataset_id,
            episode_count=len(lengths),
            total_frames=int(lengths.sum()),
            avg_episode_length=float(lengths.mean()),
            min_episode_length=int(lengths.min()),
            max_episode_length=int(lengths.max()),
            std_episode_length=float(lengths.std()),
            fps=bundle.fps,
            observation_dim=bundle.observation_dim,
            action_dim=bundle.action_dim,
            session_count=len(sessions),
            sessions=sessions,
        )

    # ------------------------------------------------------------------
    # Joint occupancy maps
    # ------------------------------------------------------------------

    def compute_joint_occupancy(
        self,
        bundle: DatasetBundle,
        joint_pairs: list[tuple[int, int]] | None = None,
    ) -> list[JointOccupancyMap]:
        """Compute 2D histograms of joint position co-occurrence.

        If joint_pairs is None, generates maps for meaningful pairs:
        XY, XZ, YZ for each arm, and left-vs-right mirrored pairs.
        """
        if not bundle.episodes:
            return []

        all_qpos = np.concatenate([ep.qpos for ep in bundle.episodes], axis=0)

        if joint_pairs is None:
            joint_pairs = self._default_joint_pairs(bundle.observation_dim)

        maps: list[JointOccupancyMap] = []
        for jx, jy in joint_pairs:
            if jx >= all_qpos.shape[1] or jy >= all_qpos.shape[1]:
                continue
            hist, x_edges, y_edges = np.histogram2d(
                all_qpos[:, jx],
                all_qpos[:, jy],
                bins=self.n_occupancy_bins,
            )
            maps.append(
                JointOccupancyMap(
                    joint_x=jx,
                    joint_y=jy,
                    joint_x_name=bundle.joint_labels[jx] if jx < len(bundle.joint_labels) else f"joint_{jx}",
                    joint_y_name=bundle.joint_labels[jy] if jy < len(bundle.joint_labels) else f"joint_{jy}",
                    x_edges=x_edges.tolist(),
                    y_edges=y_edges.tolist(),
                    histogram=hist.astype(int).T.tolist(),
                )
            )
        return maps

    @staticmethod
    def _default_joint_pairs(obs_dim: int) -> list[tuple[int, int]]:
        """Generate default joint pairs for visualization."""
        pairs: list[tuple[int, int]] = []
        if obs_dim >= 16:
            # Right arm: XY, XZ, YZ
            pairs.extend([(0, 1), (0, 2), (1, 2)])
            # Left arm: XY, XZ, YZ
            pairs.extend([(8, 9), (8, 10), (9, 10)])
            # Cross-arm: right_x vs left_x, right_y vs left_y, right_z vs left_z
            pairs.extend([(0, 8), (1, 9), (2, 10)])
            # Grippers: right vs left
            pairs.append((7, 15))
        elif obs_dim >= 6:
            # Generic: first 3 pairs
            pairs.extend([(0, 1), (0, 2), (1, 2)])
        else:
            # Minimal: sequential pairs
            for i in range(min(obs_dim - 1, 3)):
                pairs.append((i, i + 1))
        return pairs

    # ------------------------------------------------------------------
    # Temporal visitation maps
    # ------------------------------------------------------------------

    def compute_temporal_visitation(
        self,
        bundle: DatasetBundle,
        joint_indices: list[int] | None = None,
    ) -> list[TemporalVisitationMap]:
        """Compute time-resolved joint value distributions.

        Normalizes each episode's timesteps to [0, 1] and bins joint values
        against normalized time.
        """
        if not bundle.episodes:
            return []

        if joint_indices is None:
            joint_indices = self._default_temporal_joints(bundle.observation_dim)

        # Build per-joint (time, value) arrays across all episodes
        joint_data: dict[int, tuple[list[NDArray], list[NDArray]]] = {j: ([], []) for j in joint_indices}

        for ep in bundle.episodes:
            if ep.num_frames < 2:
                continue
            norm_time = np.linspace(0.0, 1.0, ep.num_frames)
            for j in joint_indices:
                if j < ep.qpos.shape[1]:
                    joint_data[j][0].append(norm_time)
                    joint_data[j][1].append(ep.qpos[:, j])

        maps: list[TemporalVisitationMap] = []
        for j in joint_indices:
            times_list, values_list = joint_data[j]
            if not times_list:
                continue
            all_times = np.concatenate(times_list)
            all_values = np.concatenate(values_list)

            hist, time_edges, value_edges = np.histogram2d(
                all_times,
                all_values,
                bins=[self.n_temporal_bins, self.n_occupancy_bins],
            )

            maps.append(
                TemporalVisitationMap(
                    joint_index=j,
                    joint_name=bundle.joint_labels[j] if j < len(bundle.joint_labels) else f"joint_{j}",
                    time_edges=time_edges.tolist(),
                    value_edges=value_edges.tolist(),
                    histogram=hist.astype(int).T.tolist(),
                )
            )
        return maps

    @staticmethod
    def _default_temporal_joints(obs_dim: int) -> list[int]:
        """Select joints for temporal maps (position components + grippers)."""
        if obs_dim >= 16:
            # XYZ for each arm + both grippers
            return [0, 1, 2, 7, 8, 9, 10, 15]
        return list(range(min(obs_dim, 6)))

    # ------------------------------------------------------------------
    # Object detection sampling
    # ------------------------------------------------------------------

    def sample_frames_for_detection(
        self,
        dataset_path: Path,
        bundle: DatasetBundle,
    ) -> list[tuple[str, int, int, NDArray[np.uint8]]]:
        """Sample frames across the dataset for object detection.

        Returns list of (session_id, episode_index, frame_index, image_array).
        """
        if not HDF5_AVAILABLE:
            return []

        candidates: list[tuple[str, int, int, Path]] = []
        sessions = self.discover_sessions(dataset_path)

        for session_id, session_path in sessions:
            hdf5_files = sorted(session_path.glob("*.hdf5"), key=lambda p: _episode_sort_key(p))
            for ep_idx, hdf5_path in enumerate(hdf5_files):
                ep = next(
                    (e for e in bundle.episodes if e.session_id == session_id and e.episode_index == ep_idx),
                    None,
                )
                if ep is None or not ep.has_images:
                    continue
                # Sample from start, middle, end of each episode
                for frac in (0.1, 0.5, 0.9):
                    frame_idx = int(frac * (ep.num_frames - 1))
                    candidates.append((session_id, ep_idx, frame_idx, hdf5_path))

        n_samples = min(self.n_detection_samples, len(candidates))
        if n_samples == 0:
            return []

        rng = random.Random(42)
        selected = rng.sample(candidates, n_samples)

        frames: list[tuple[str, int, int, NDArray[np.uint8]]] = []
        for session_id, ep_idx, frame_idx, hdf5_path in selected:
            try:
                with h5py.File(hdf5_path, "r") as f:
                    # Find the first available camera
                    images_group = f.get("observations/images")
                    if images_group is None:
                        continue
                    camera_names = list(images_group.keys())
                    if not camera_names:
                        continue
                    image = np.array(images_group[camera_names[0]][frame_idx], dtype=np.uint8)
                    frames.append((session_id, ep_idx, frame_idx, image))
            except Exception:
                logger.debug("Failed to read frame %d from %s", frame_idx, hdf5_path, exc_info=True)
        return frames

    def run_detection_summary(
        self,
        frames: list[tuple[str, int, int, NDArray[np.uint8]]],
    ) -> DetectionSummary:
        """Run YOLO detection on sampled frames and aggregate results."""
        try:
            from ultralytics import YOLO
        except ImportError:
            logger.info("ultralytics not installed; skipping object detection")
            return DetectionSummary(total_frames_sampled=0, total_detections=0)

        model = YOLO("yolo11n.pt")

        class_counts: dict[str, int] = {}
        class_frame_counts: dict[str, int] = {}
        class_confidences: dict[str, list[float]] = {}
        samples: list[DetectionSample] = []
        total_detections = 0

        for session_id, ep_idx, frame_idx, image in frames:
            results = model(image, conf=self.detection_confidence, verbose=False)
            frame_detections: list[dict] = []
            frame_classes: set[str] = set()

            for result in results:
                for box in result.boxes:
                    cls_id = int(box.cls[0])
                    cls_name = result.names[cls_id]
                    conf = float(box.conf[0])
                    bbox = box.xyxy[0].tolist()

                    frame_detections.append(
                        {
                            "class": cls_name,
                            "confidence": round(conf, 3),
                            "bbox": [round(v, 1) for v in bbox],
                        }
                    )
                    class_counts[cls_name] = class_counts.get(cls_name, 0) + 1
                    class_confidences.setdefault(cls_name, []).append(conf)
                    frame_classes.add(cls_name)
                    total_detections += 1

            for cls_name in frame_classes:
                class_frame_counts[cls_name] = class_frame_counts.get(cls_name, 0) + 1

            samples.append(
                DetectionSample(
                    session_id=session_id,
                    episode_index=ep_idx,
                    frame_index=frame_idx,
                    detections=frame_detections,
                )
            )

        detected_classes = [
            DetectedObjectClass(
                class_name=cls_name,
                total_count=class_counts[cls_name],
                frame_count=class_frame_counts.get(cls_name, 0),
                avg_confidence=round(float(np.mean(class_confidences[cls_name])), 3),
            )
            for cls_name in sorted(class_counts, key=lambda c: class_counts[c], reverse=True)
        ]

        return DetectionSummary(
            total_frames_sampled=len(frames),
            total_detections=total_detections,
            detected_classes=detected_classes,
            samples=samples,
        )

    # ------------------------------------------------------------------
    # Full analysis pipeline
    # ------------------------------------------------------------------

    def analyze(
        self,
        dataset_id: str,
        dataset_path: Path,
        include_detection: bool = False,
        joint_pairs: list[tuple[int, int]] | None = None,
        temporal_joints: list[int] | None = None,
    ) -> AggregateAnalysisResult:
        """Run the full aggregate analysis pipeline."""
        bundle = self.load_dataset(dataset_path)
        statistics = self.compute_statistics(dataset_id, bundle)
        occupancy_maps = self.compute_joint_occupancy(bundle, joint_pairs)
        temporal_maps = self.compute_temporal_visitation(bundle, temporal_joints)

        detection_summary = None
        if include_detection:
            frames = self.sample_frames_for_detection(dataset_path, bundle)
            if frames:
                detection_summary = self.run_detection_summary(frames)

        return AggregateAnalysisResult(
            statistics=statistics,
            occupancy_maps=occupancy_maps,
            temporal_maps=temporal_maps,
            detection_summary=detection_summary,
        )


def _episode_sort_key(path: Path) -> tuple[str, int]:
    """Sort episode files by name, extracting numeric suffix for natural ordering."""
    stem = path.stem
    parts = stem.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return (parts[0], int(parts[1]))
    return (stem, 0)


# Singleton
_analyzer: AggregateAnalyzer | None = None


def get_aggregate_analyzer() -> AggregateAnalyzer:
    """Get or create the singleton AggregateAnalyzer instance."""
    global _analyzer
    if _analyzer is None:
        _analyzer = AggregateAnalyzer()
    return _analyzer
