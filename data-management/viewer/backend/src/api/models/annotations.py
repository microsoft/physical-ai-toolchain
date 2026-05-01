"""
Annotation Pydantic models for robotic episode annotation system.

These models match the TypeScript type definitions and PRD schema specifications
for task completeness, trajectory quality, data quality, and anomaly annotations.
"""

from datetime import datetime
from enum import Enum, StrEnum
from typing import Annotated, ClassVar

from pydantic import Field

from ..validation import SanitizedModel

# ============================================================================
# Task Completeness Types
# ============================================================================


class TaskCompletenessRating(StrEnum):
    """Task completion status rating."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"
    UNKNOWN = "unknown"


class ConfidenceLevel(int, Enum):
    """Annotator confidence level (1-5 scale)."""

    ONE = 1
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5


class TaskCompletenessAnnotation(SanitizedModel):
    """Task completeness annotation with rating and optional details."""

    rating: TaskCompletenessRating
    confidence: ConfidenceLevel
    completion_percentage: int | None = Field(None, ge=0, le=100)
    failure_reason: str | None = None
    subtask_reached: str | None = None

    model_config: ClassVar = {"use_enum_values": True}


# ============================================================================
# Trajectory Quality Types
# ============================================================================


class QualityScore(int, Enum):
    """Quality score on a 1-5 scale."""

    ONE = 1
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5


class TrajectoryFlag(StrEnum):
    """Flags indicating specific trajectory issues."""

    JITTERY = "jittery"
    INEFFICIENT_PATH = "inefficient-path"
    NEAR_COLLISION = "near-collision"
    OVER_EXTENSION = "over-extension"
    UNDER_REACHING = "under-reaching"
    HESITATION = "hesitation"
    CORRECTION_HEAVY = "correction-heavy"


class TrajectoryQualityMetrics(SanitizedModel):
    """Individual trajectory quality metrics."""

    smoothness: QualityScore
    efficiency: QualityScore
    safety: QualityScore
    precision: QualityScore

    model_config: ClassVar = {"use_enum_values": True}


class TrajectoryQualityAnnotation(SanitizedModel):
    """Complete trajectory quality annotation."""

    overall_score: QualityScore
    metrics: TrajectoryQualityMetrics
    flags: list[TrajectoryFlag] = Field(default_factory=list)

    model_config: ClassVar = {"use_enum_values": True}


# ============================================================================
# Data Quality Types
# ============================================================================


class DataQualityLevel(StrEnum):
    """Overall data quality level."""

    GOOD = "good"
    ACCEPTABLE = "acceptable"
    POOR = "poor"
    UNUSABLE = "unusable"


class DataQualityIssueType(StrEnum):
    """Types of data quality issues."""

    FRAME_DROP = "frame-drop"
    SYNC_ISSUE = "sync-issue"
    OCCLUSION = "occlusion"
    LIGHTING_ISSUE = "lighting-issue"
    SENSOR_NOISE = "sensor-noise"
    CALIBRATION_DRIFT = "calibration-drift"
    ENCODING_ARTIFACT = "encoding-artifact"
    MISSING_DATA = "missing-data"


class IssueSeverity(StrEnum):
    """Severity of a data quality issue."""

    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class DataQualityIssue(SanitizedModel):
    """Individual data quality issue."""

    type: DataQualityIssueType
    severity: IssueSeverity
    affected_frames: tuple[int, int] | None = None
    affected_streams: list[str] | None = None
    notes: str | None = None

    model_config: ClassVar = {"use_enum_values": True}


class DataQualityAnnotation(SanitizedModel):
    """Complete data quality annotation."""

    overall_quality: DataQualityLevel
    issues: list[DataQualityIssue] = Field(default_factory=list)

    model_config: ClassVar = {"use_enum_values": True}


# ============================================================================
# Anomaly Types
# ============================================================================


class AnomalySeverity(StrEnum):
    """Severity of an anomaly."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AnomalyType(StrEnum):
    """Types of anomalies that can be detected."""

    UNEXPECTED_STOP = "unexpected-stop"
    TRAJECTORY_DEVIATION = "trajectory-deviation"
    FORCE_SPIKE = "force-spike"
    VELOCITY_SPIKE = "velocity-spike"
    OBJECT_SLIP = "object-slip"
    GRIPPER_FAILURE = "gripper-failure"
    COLLISION = "collision"
    OTHER = "other"


class Anomaly(SanitizedModel):
    """Individual anomaly marker."""

    id: str
    type: AnomalyType
    severity: AnomalySeverity
    frame_range: tuple[int, int]
    timestamp: tuple[float, float]
    description: str
    auto_detected: bool = False
    verified: bool = False

    model_config: ClassVar = {"use_enum_values": True}


class AnomalyAnnotation(SanitizedModel):
    """Container for anomaly annotations."""

    anomalies: list[Anomaly] = Field(default_factory=list)


# ============================================================================
# Language Instruction Types (VLA)
# ============================================================================


class InstructionSource(StrEnum):
    """Source of the language instruction annotation."""

    HUMAN = "human"
    TEMPLATE = "template"
    LLM_GENERATED = "llm-generated"
    RETROACTIVE = "retroactive"


class LanguageInstructionAnnotation(SanitizedModel):
    """Natural language instruction for VLA-conditioned training.

    Stores the primary task instruction plus optional paraphrases and
    subtask decomposition used for data augmentation and hierarchical
    policy conditioning.
    """

    instruction: str = Field(min_length=1, max_length=1000)
    source: InstructionSource
    language: str = Field(default="en", max_length=10)
    paraphrases: list[Annotated[str, Field(max_length=1000)]] = Field(default_factory=list, max_length=50)
    subtask_instructions: list[Annotated[str, Field(max_length=1000)]] = Field(default_factory=list, max_length=100)

    model_config: ClassVar = {"use_enum_values": True}


# ============================================================================
# Combined Episode Annotation Types
# ============================================================================


class EpisodeAnnotation(SanitizedModel):
    """Complete annotation for a single episode by one annotator."""

    annotator_id: str
    timestamp: datetime
    task_completeness: TaskCompletenessAnnotation
    trajectory_quality: TrajectoryQualityAnnotation
    data_quality: DataQualityAnnotation
    anomalies: AnomalyAnnotation
    language_instruction: LanguageInstructionAnnotation | None = None
    notes: str | None = None


class EpisodeConsensus(SanitizedModel):
    """Consensus annotation derived from multiple annotators."""

    task_completeness: TaskCompletenessRating
    trajectory_score: float = Field(ge=1.0, le=5.0)
    data_quality: DataQualityLevel
    agreement_score: float = Field(ge=0.0, le=1.0)

    model_config: ClassVar = {"use_enum_values": True}


class EpisodeAnnotationFile(SanitizedModel):
    """Complete annotation file for an episode."""

    schema_version: str = "1.0.0"
    episode_index: int = Field(ge=0)
    dataset_id: str
    annotations: list[EpisodeAnnotation] = Field(default_factory=list)
    consensus: EpisodeConsensus | None = None


# ============================================================================
# Auto-Analysis Types
# ============================================================================


class ComputedQualityMetrics(SanitizedModel):
    """Computed trajectory quality metrics from auto-analysis."""

    smoothness_score: float = Field(ge=0.0, le=1.0)
    efficiency_score: float = Field(ge=0.0, le=1.0)
    jitter_metric: float = Field(ge=0.0)
    hesitation_count: int = Field(ge=0)
    correction_count: int = Field(ge=0)


class AutoQualityAnalysis(SanitizedModel):
    """Auto-analysis result for an episode."""

    episode_index: int = Field(ge=0)
    computed: ComputedQualityMetrics
    suggested_rating: int = Field(ge=1, le=5)
    confidence: float = Field(ge=0.0, le=1.0)
    flags: list[TrajectoryFlag] = Field(default_factory=list)

    model_config: ClassVar = {"use_enum_values": True}


# ============================================================================
# Annotation Summary Types
# ============================================================================


class AnnotationSummary(SanitizedModel):
    """Aggregated annotation metrics for a dataset."""

    dataset_id: str
    total_episodes: int = Field(ge=0)
    annotated_episodes: int = Field(ge=0)
    task_completeness_distribution: dict[str, int] = Field(default_factory=dict)
    quality_score_distribution: dict[int, int] = Field(default_factory=dict)
    anomaly_type_counts: dict[str, int] = Field(default_factory=dict)
