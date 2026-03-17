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
