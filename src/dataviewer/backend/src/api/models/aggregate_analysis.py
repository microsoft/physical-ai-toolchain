"""Pydantic models for dataset aggregate analysis."""

from pydantic import BaseModel, Field


class SessionStatistics(BaseModel):
    """Per-session statistics within a nested dataset."""

    session_id: str = Field(description="Session directory name (timestamp)")
    episode_count: int = Field(ge=0, description="Number of episodes in this session")
    total_frames: int = Field(ge=0, description="Total frames across all episodes")
    avg_episode_length: float = Field(ge=0, description="Average episode length in frames")
    min_episode_length: int = Field(ge=0, description="Shortest episode in frames")
    max_episode_length: int = Field(ge=0, description="Longest episode in frames")


class DatasetStatistics(BaseModel):
    """Aggregate statistics for an entire dataset."""

    dataset_id: str = Field(description="Dataset identifier")
    episode_count: int = Field(ge=0, description="Total number of episodes")
    total_frames: int = Field(ge=0, description="Total frames across all episodes")
    avg_episode_length: float = Field(ge=0, description="Average episode length in frames")
    min_episode_length: int = Field(ge=0, description="Shortest episode in frames")
    max_episode_length: int = Field(ge=0, description="Longest episode in frames")
    std_episode_length: float = Field(ge=0, description="Standard deviation of episode lengths")
    fps: float = Field(gt=0, description="Frames per second")
    observation_dim: int = Field(ge=0, description="Observation vector dimension")
    action_dim: int = Field(ge=0, description="Action vector dimension")
    session_count: int = Field(ge=0, description="Number of recording sessions")
    sessions: list[SessionStatistics] = Field(default_factory=list, description="Per-session breakdown")


class JointOccupancyMap(BaseModel):
    """2D histogram of joint position co-occurrence for a pair of joints."""

    joint_x: int = Field(ge=0, description="Index of the X-axis joint")
    joint_y: int = Field(ge=0, description="Index of the Y-axis joint")
    joint_x_name: str = Field(description="Label for the X-axis joint")
    joint_y_name: str = Field(description="Label for the Y-axis joint")
    x_edges: list[float] = Field(description="Bin edges for X-axis")
    y_edges: list[float] = Field(description="Bin edges for Y-axis")
    histogram: list[list[int]] = Field(description="2D histogram counts (rows=Y bins, cols=X bins)")


class TemporalVisitationMap(BaseModel):
    """Joint value distribution over normalized episode time."""

    joint_index: int = Field(ge=0, description="Index of the joint")
    joint_name: str = Field(description="Label for the joint")
    time_edges: list[float] = Field(description="Normalized time bin edges (0 to 1)")
    value_edges: list[float] = Field(description="Joint value bin edges")
    histogram: list[list[int]] = Field(description="2D histogram (rows=value bins, cols=time bins)")


class DetectedObjectClass(BaseModel):
    """Aggregated object detection result for a single class."""

    class_name: str = Field(description="Detected object class name")
    total_count: int = Field(ge=0, description="Total detections across sampled frames")
    frame_count: int = Field(ge=0, description="Number of frames containing this class")
    avg_confidence: float = Field(ge=0, le=1, description="Average detection confidence")


class DetectionSample(BaseModel):
    """Detection result for a single sampled frame."""

    session_id: str = Field(description="Session the frame came from")
    episode_index: int = Field(ge=0, description="Episode index within the session")
    frame_index: int = Field(ge=0, description="Frame index within the episode")
    detections: list[dict] = Field(description="Detection boxes with class, confidence, bbox")


class DetectionSummary(BaseModel):
    """Aggregate object detection summary across sampled frames."""

    total_frames_sampled: int = Field(ge=0, description="Number of frames sampled for detection")
    total_detections: int = Field(ge=0, description="Total detection count")
    detected_classes: list[DetectedObjectClass] = Field(
        default_factory=list, description="Per-class aggregated results"
    )
    samples: list[DetectionSample] = Field(default_factory=list, description="Per-frame detection details")


class AggregateAnalysisResult(BaseModel):
    """Complete aggregate analysis result for a dataset."""

    statistics: DatasetStatistics
    occupancy_maps: list[JointOccupancyMap] = Field(default_factory=list, description="Joint pair occupancy heatmaps")
    temporal_maps: list[TemporalVisitationMap] = Field(
        default_factory=list, description="Per-joint temporal visitation heatmaps"
    )
    detection_summary: DetectionSummary | None = Field(
        default=None, description="Object detection summary (None if detection was not requested)"
    )
