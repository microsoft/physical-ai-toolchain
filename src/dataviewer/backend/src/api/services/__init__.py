"""
Services package for business logic.

Provides service layer abstractions for dataset and annotation operations,
as well as AI analysis services.
"""

from .annotation_service import AnnotationService, get_annotation_service
from .anomaly_detection import (
    AnomalyDetector,
    AnomalySeverity,
    AnomalyType,
    DetectedAnomaly,
)
from .clustering import ClusterAssignment, ClusteringResult, EpisodeClusterer
from .dataset_service import DatasetService, get_dataset_service
from .trajectory_analysis import TrajectoryAnalyzer, TrajectoryMetrics

__all__ = [
    # Dataset and Annotation Services
    "AnnotationService",
    # Anomaly Detection
    "AnomalyDetector",
    "AnomalySeverity",
    "AnomalyType",
    "ClusterAssignment",
    "ClusteringResult",
    "DatasetService",
    "DetectedAnomaly",
    # Clustering
    "EpisodeClusterer",
    # Trajectory Analysis
    "TrajectoryAnalyzer",
    "TrajectoryMetrics",
    "get_annotation_service",
    "get_dataset_service",
]


# Lazy import for optional HDF5 dependency
def get_hdf5_loader():
    """Get the HDF5 loader (requires h5py)."""
    from .hdf5_loader import HDF5Loader
    from .hdf5_loader import get_hdf5_loader as _get_loader

    return HDF5Loader, _get_loader


def get_hdf5_exporter():
    """Get the HDF5 exporter (requires h5py and Pillow)."""
    from .hdf5_exporter import HDF5Exporter, parse_edit_operations

    return HDF5Exporter, parse_edit_operations


def get_image_transform():
    """Get the image transform service (requires Pillow for resize)."""
    from .image_transform import (
        CropRegion,
        ImageTransform,
        ResizeDimensions,
        apply_transform,
        apply_transforms_batch,
    )

    return ImageTransform, CropRegion, ResizeDimensions, apply_transform, apply_transforms_batch
