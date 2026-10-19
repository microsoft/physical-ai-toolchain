"""
Data source Pydantic models for LeRobot annotation system.

Supports local filesystem, Azure Blob Storage, and Hugging Face Hub data sources.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

# ============================================================================
# Data Source Types
# ============================================================================


class LocalDataSource(BaseModel):
    """Local filesystem data source for edge device or development."""

    type: Literal["local"] = "local"
    path: str = Field(description="Absolute or relative path to dataset directory")
    watch_for_changes: bool = Field(default=False, description="Whether to watch for file changes")


class AzureBlobDataSource(BaseModel):
    """Azure Blob Storage data source for cloud deployment."""

    type: Literal["azure-blob"] = "azure-blob"
    account_name: str = Field(description="Azure storage account name")
    container_name: str = Field(description="Blob container name")
    sas_token: str | None = Field(default=None, description="SAS token for authentication")
    managed_identity: bool = Field(default=False, description="Use managed identity for authentication")


class HuggingFaceDataSource(BaseModel):
    """Hugging Face Hub data source for public/private datasets."""

    type: Literal["huggingface"] = "huggingface"
    repo_id: str = Field(description="Repository ID in format 'owner/repo'")
    revision: str | None = Field(default=None, description="Git revision (branch, tag, or commit hash)")
    token: str | None = Field(default=None, description="Hugging Face API token for private repos")


# Discriminated union for data sources
DataSource = Annotated[
    LocalDataSource | AzureBlobDataSource | HuggingFaceDataSource,
    Field(discriminator="type"),
]


# ============================================================================
# Dataset Metadata Types
# ============================================================================


class FeatureSchema(BaseModel):
    """Schema definition for a dataset feature."""

    dtype: str = Field(description="Data type (e.g., 'float32', 'int64')")
    shape: list[int] = Field(description="Shape of the feature array")
    names: list[str] | None = Field(default=None, description="Optional per-dimension feature names")


class TaskInfo(BaseModel):
    """Task definition within a dataset."""

    task_index: int = Field(ge=0, description="Task index identifier")
    description: str = Field(description="Human-readable task description")


class DatasetInfo(BaseModel):
    """Complete dataset metadata."""

    id: str = Field(description="Unique dataset identifier")
    name: str = Field(description="Human-readable dataset name")
    group: str | None = Field(default=None, description="Parent folder group for nested datasets")
    total_episodes: int = Field(ge=0, description="Total number of episodes")
    fps: float = Field(gt=0, description="Frames per second")
    features: dict[str, FeatureSchema] = Field(default_factory=dict, description="Feature schemas by name")
    tasks: list[TaskInfo] = Field(default_factory=list, description="Available tasks")


class AcceptedDatasetContract(BaseModel):
    """Dataset adapter identities and verified capture artifact digests."""

    dataset_id: str
    output_adapter_id: str
    output_adapter_version: str
    viewer_adapter_id: str
    profile_id: str
    profile_sha256: str
    capture_provenance_sha256: str
    export_validation_sha256: str
    capture_features: list[dict[str, object]]
    sensors: list[dict[str, object]]


# ============================================================================
# Episode Types
# ============================================================================


class EpisodeMeta(BaseModel):
    """Episode metadata for list views."""

    index: int = Field(ge=0, description="Episode index within the dataset")
    length: int = Field(ge=0, description="Number of frames in the episode")
    task_index: int = Field(ge=0, description="Task index this episode belongs to")
    has_annotations: bool = Field(default=False, description="Whether this episode has annotations")


class TrajectoryVariable(BaseModel):
    """Named per-frame variable available for trajectory plotting."""

    key: str = Field(description="Stable variable key used in trajectory point values")
    label: str = Field(description="Human-readable variable label")
    source: str = Field(description="Dataset feature this variable came from")
    index: int | None = Field(default=None, ge=0, description="Feature dimension index for vector values")
    kind: str = Field(default="signal", description="Variable category such as state, action, or signal")


class TrajectoryPoint(BaseModel):
    """Single trajectory data point."""

    timestamp: float = Field(ge=0, description="Timestamp in seconds")
    frame: int = Field(ge=0, description="Frame index")
    joint_positions: list[float] = Field(description="Joint positions array")
    joint_velocities: list[float] = Field(description="Joint velocities array")
    end_effector_pose: list[float] = Field(description="End-effector pose (position + orientation)")
    gripper_state: float = Field(ge=0, le=1, description="Gripper state (0=open, 1=closed)")
    variables: dict[str, float] = Field(default_factory=dict, description="Named plottable variable values")


class FrameInsertion(BaseModel):
    """Specification for an interpolated frame insertion."""

    after_frame_index: int = Field(
        ge=0,
        description="Insert after this frame index (original index space)",
    )
    interpolation_factor: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Interpolation factor between frames (0.0-1.0)",
    )


class RecordedSubtask(BaseModel):
    """A subtask recorded with an episode, which the subtask editor starts from."""

    id: str = Field(description="Stable segment ID")
    label: str = Field(description="Subtask label")
    frame_range: tuple[int, int] = Field(description="First and last frame, inclusive")
    color: str | None = Field(default=None, description="Display color (hex), when recorded")
    source: str = Field(description="How the subtask was created: manual, auto or recorded")
    description: str | None = Field(default=None, description="Optional description")


class EpisodeData(BaseModel):
    """Complete episode data for viewing."""

    meta: EpisodeMeta
    video_urls: dict[str, str] = Field(default_factory=dict, description="Video URLs by camera name")
    video_time_windows: dict[str, list[float]] = Field(
        default_factory=dict,
        description="Per-camera [start, end] timestamps within the (possibly concatenated) video file",
    )
    cameras: list[str] = Field(default_factory=list, description="Available camera names")
    trajectory_variables: list[TrajectoryVariable] = Field(
        default_factory=list,
        description="Named variables available for trajectory plots",
    )
    trajectory_data: list[TrajectoryPoint] = Field(default_factory=list, description="Trajectory data points")
    subtasks: list[RecordedSubtask] = Field(default_factory=list, description="Subtasks recorded with the episode")


# ============================================================================
# Clustering Types
# ============================================================================


class SimilarityLevel(str):
    """Similarity level for episode clusters."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EpisodeCluster(BaseModel):
    """Episode cluster from similarity analysis."""

    id: str = Field(description="Cluster identifier")
    centroid_episode: int = Field(ge=0, description="Index of centroid episode")
    members: list[int] = Field(default_factory=list, description="Episode indices")
    similarity: str = Field(default="medium", description="Similarity level")
    suggested_label: str | None = Field(default=None, description="Suggested label if consensus exists")


class EpisodeClustering(BaseModel):
    """Clustering results for a dataset."""

    clusters: list[EpisodeCluster] = Field(default_factory=list)
