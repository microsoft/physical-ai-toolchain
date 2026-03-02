"""
AI analysis API endpoints.

Provides endpoints for trajectory analysis, anomaly detection,
and episode clustering.
"""

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import (
    AnomalyDetector,
    AnomalySeverity,
    DetectedAnomaly,
    EpisodeClusterer,
    TrajectoryAnalyzer,
    TrajectoryMetrics,
)

router = APIRouter(prefix="/ai", tags=["ai"])


# Request/Response Models


class TrajectoryData(BaseModel):
    """Trajectory data for analysis."""

    positions: list[list[float]] = Field(description="Joint positions array of shape (N, num_joints)")
    timestamps: list[float] = Field(description="Timestamps for each position")
    gripper_states: list[float] | None = Field(default=None, description="Optional gripper states")


class TrajectoryMetricsResponse(BaseModel):
    """Response with computed trajectory metrics."""

    smoothness: float = Field(description="Smoothness score (0-1)")
    efficiency: float = Field(description="Path efficiency score (0-1)")
    jitter: float = Field(description="Jitter level (lower is better)")
    hesitation_count: int = Field(description="Number of hesitation events")
    correction_count: int = Field(description="Number of direction corrections")
    overall_score: int = Field(ge=1, le=5, description="Overall quality score (1-5)")
    flags: list[str] = Field(description="Detected quality flags")

    @classmethod
    def from_metrics(cls, metrics: TrajectoryMetrics) -> "TrajectoryMetricsResponse":
        """Create from TrajectoryMetrics dataclass."""
        return cls(
            smoothness=metrics.smoothness,
            efficiency=metrics.efficiency,
            jitter=metrics.jitter,
            hesitation_count=metrics.hesitation_count,
            correction_count=metrics.correction_count,
            overall_score=metrics.overall_score,
            flags=metrics.flags,
        )


class AnomalyResponse(BaseModel):
    """Detected anomaly response."""

    id: str = Field(description="Unique identifier")
    type: str = Field(description="Anomaly type")
    severity: str = Field(description="Severity level")
    frame_start: int = Field(description="Start frame index")
    frame_end: int = Field(description="End frame index")
    description: str = Field(description="Human-readable description")
    confidence: float = Field(ge=0, le=1, description="Detection confidence")
    auto_detected: bool = Field(default=True, description="Auto-detected flag")

    @classmethod
    def from_anomaly(cls, anomaly: DetectedAnomaly) -> "AnomalyResponse":
        """Create from DetectedAnomaly dataclass."""
        return cls(
            id=anomaly.id,
            type=anomaly.type.value,
            severity=anomaly.severity.value,
            frame_start=anomaly.frame_range[0],
            frame_end=anomaly.frame_range[1],
            description=anomaly.description,
            confidence=anomaly.confidence,
            auto_detected=anomaly.auto_detected,
        )


class AnomalyDetectionRequest(BaseModel):
    """Request for anomaly detection."""

    positions: list[list[float]] = Field(description="Joint positions")
    timestamps: list[float] = Field(description="Timestamps")
    forces: list[list[float]] | None = Field(default=None, description="Optional force/torque data")
    gripper_states: list[float] | None = Field(default=None, description="Optional gripper states")
    gripper_commands: list[float] | None = Field(default=None, description="Optional gripper commands")


class AnomalyDetectionResponse(BaseModel):
    """Response with detected anomalies."""

    anomalies: list[AnomalyResponse] = Field(description="List of detected anomalies")
    total_count: int = Field(description="Total number of anomalies")
    severity_counts: dict[str, int] = Field(description="Count of anomalies by severity")


class ClusterRequest(BaseModel):
    """Request for episode clustering."""

    trajectories: list[list[list[float]]] = Field(description="List of trajectory arrays")
    num_clusters: int | None = Field(default=None, ge=2, le=20, description="Optional fixed number of clusters")


class ClusterAssignmentResponse(BaseModel):
    """Cluster assignment for an episode."""

    episode_index: int = Field(description="Episode index")
    cluster_id: int = Field(description="Assigned cluster ID")
    similarity_score: float = Field(description="Similarity to cluster centroid")


class ClusterResponse(BaseModel):
    """Clustering result response."""

    num_clusters: int = Field(description="Number of clusters found")
    assignments: list[ClusterAssignmentResponse] = Field(description="Cluster assignments")
    cluster_sizes: dict[str, int] = Field(description="Size of each cluster")
    silhouette_score: float = Field(description="Clustering quality score")


class SuggestAnnotationRequest(BaseModel):
    """Request for AI-suggested annotations."""

    positions: list[list[float]] = Field(description="Joint positions")
    timestamps: list[float] = Field(description="Timestamps")
    gripper_states: list[float] | None = Field(default=None, description="Optional gripper states")
    forces: list[list[float]] | None = Field(default=None, description="Optional force data")


class AnnotationSuggestion(BaseModel):
    """AI-suggested annotation."""

    task_completion_rating: int = Field(ge=1, le=5, description="Suggested task completion rating")
    trajectory_quality_score: int = Field(ge=1, le=5, description="Suggested trajectory quality score")
    suggested_flags: list[str] = Field(description="Suggested quality flags")
    detected_anomalies: list[AnomalyResponse] = Field(description="Detected anomalies")
    confidence: float = Field(ge=0, le=1, description="Overall suggestion confidence")
    reasoning: str = Field(description="Explanation for suggestions")


# API Endpoints


@router.post("/trajectory-analysis", response_model=TrajectoryMetricsResponse)
async def analyze_trajectory(data: TrajectoryData) -> TrajectoryMetricsResponse:
    """
    Analyze trajectory quality and compute metrics.

    Computes smoothness, efficiency, jitter, hesitations, and corrections
    from the provided trajectory data.
    """
    if len(data.positions) < 3:
        raise HTTPException(status_code=400, detail="Trajectory must have at least 3 positions")

    if len(data.positions) != len(data.timestamps):
        raise HTTPException(status_code=400, detail="Positions and timestamps must have same length")

    positions = np.array(data.positions)
    timestamps = np.array(data.timestamps)
    gripper_states = np.array(data.gripper_states) if data.gripper_states else None

    analyzer = TrajectoryAnalyzer()
    metrics = analyzer.analyze(positions, timestamps, gripper_states)

    return TrajectoryMetricsResponse.from_metrics(metrics)


@router.post("/anomaly-detection", response_model=AnomalyDetectionResponse)
async def detect_anomalies(request: AnomalyDetectionRequest) -> AnomalyDetectionResponse:
    """
    Detect anomalies in a trajectory.

    Analyzes the trajectory for velocity spikes, force spikes,
    unexpected stops, oscillations, and other anomalies.
    """
    if len(request.positions) < 3:
        raise HTTPException(status_code=400, detail="Trajectory must have at least 3 positions")

    positions = np.array(request.positions)
    timestamps = np.array(request.timestamps)
    forces = np.array(request.forces) if request.forces else None
    gripper_states = np.array(request.gripper_states) if request.gripper_states else None
    gripper_commands = np.array(request.gripper_commands) if request.gripper_commands else None

    detector = AnomalyDetector()
    anomalies = detector.detect(
        positions=positions,
        timestamps=timestamps,
        forces=forces,
        gripper_states=gripper_states,
        gripper_commands=gripper_commands,
    )

    # Count by severity
    severity_counts = {"low": 0, "medium": 0, "high": 0}
    for anomaly in anomalies:
        severity_counts[anomaly.severity.value] += 1

    return AnomalyDetectionResponse(
        anomalies=[AnomalyResponse.from_anomaly(a) for a in anomalies],
        total_count=len(anomalies),
        severity_counts=severity_counts,
    )


@router.post("/cluster", response_model=ClusterResponse)
async def cluster_episodes(request: ClusterRequest) -> ClusterResponse:
    """
    Cluster similar episodes based on trajectory features.

    Uses hierarchical clustering with Ward linkage to group
    episodes with similar motion patterns.
    """
    if len(request.trajectories) < 2:
        raise HTTPException(status_code=400, detail="At least 2 trajectories required for clustering")

    trajectories = [np.array(t) for t in request.trajectories]

    clusterer = EpisodeClusterer()
    result = clusterer.cluster(trajectories, request.num_clusters)

    return ClusterResponse(
        num_clusters=result.num_clusters,
        assignments=[
            ClusterAssignmentResponse(
                episode_index=a.episode_index,
                cluster_id=a.cluster_id,
                similarity_score=a.similarity_score,
            )
            for a in result.assignments
        ],
        cluster_sizes={str(k): v for k, v in result.cluster_sizes.items()},
        silhouette_score=result.silhouette_score,
    )


@router.post("/suggest-annotation", response_model=AnnotationSuggestion)
async def suggest_annotation(request: SuggestAnnotationRequest) -> AnnotationSuggestion:
    """
    Generate AI suggestions for episode annotation.

    Analyzes the trajectory and provides suggested ratings,
    flags, and detected anomalies.
    """
    if len(request.positions) < 3:
        raise HTTPException(status_code=400, detail="Trajectory must have at least 3 positions")

    positions = np.array(request.positions)
    timestamps = np.array(request.timestamps)
    gripper_states = np.array(request.gripper_states) if request.gripper_states else None
    forces = np.array(request.forces) if request.forces else None

    # Analyze trajectory quality
    analyzer = TrajectoryAnalyzer()
    metrics = analyzer.analyze(positions, timestamps, gripper_states)

    # Detect anomalies
    detector = AnomalyDetector()
    anomalies = detector.detect(
        positions=positions,
        timestamps=timestamps,
        forces=forces,
        gripper_states=gripper_states,
    )

    # Generate suggestions based on metrics
    trajectory_quality_score = metrics.overall_score

    # Task completion is harder to infer - use trajectory quality as proxy
    # with adjustment for severe anomalies
    severe_anomaly_count = sum(1 for a in anomalies if a.severity == AnomalySeverity.HIGH)
    task_completion_rating = max(1, trajectory_quality_score - severe_anomaly_count)

    # Combine flags from metrics and anomalies
    suggested_flags = list(metrics.flags)
    if severe_anomaly_count > 0:
        suggested_flags.append("has_severe_anomalies")
    if len(anomalies) > 5:
        suggested_flags.append("many_anomalies")

    # Compute confidence based on data quality
    trajectory_length = len(positions)
    confidence = min(1.0, trajectory_length / 100.0) * 0.8
    if len(anomalies) == 0 and metrics.overall_score >= 4:
        confidence = min(confidence + 0.2, 1.0)

    # Generate reasoning
    reasoning_parts = []
    reasoning_parts.append(f"Trajectory smoothness: {metrics.smoothness:.2f}")
    reasoning_parts.append(f"Path efficiency: {metrics.efficiency:.2f}")
    if metrics.hesitation_count > 0:
        reasoning_parts.append(f"Detected {metrics.hesitation_count} hesitations")
    if metrics.correction_count > 0:
        reasoning_parts.append(f"Detected {metrics.correction_count} corrections")
    if len(anomalies) > 0:
        reasoning_parts.append(f"Found {len(anomalies)} anomalies")

    return AnnotationSuggestion(
        task_completion_rating=task_completion_rating,
        trajectory_quality_score=trajectory_quality_score,
        suggested_flags=suggested_flags,
        detected_anomalies=[AnomalyResponse.from_anomaly(a) for a in anomalies],
        confidence=confidence,
        reasoning=". ".join(reasoning_parts) + ".",
    )
