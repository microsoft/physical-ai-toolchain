/**
 * Annotation type definitions for robotic episode annotation system.
 * These types match the PRD schema definitions for task completeness,
 * trajectory quality, data quality, and anomaly annotations.
 */

// ============================================================================
// Task Completeness Types
// ============================================================================

/** Task completion status rating */
export type TaskCompletenessRating = 'success' | 'partial' | 'failure' | 'unknown'

/** Annotator confidence level (1-5 scale) */
export type ConfidenceLevel = 1 | 2 | 3 | 4 | 5

/** Task completeness annotation with rating and optional details */
export interface TaskCompletenessAnnotation {
  /** Overall task completion rating */
  rating: TaskCompletenessRating
  /** Annotator confidence in the rating */
  confidence: ConfidenceLevel
  /** Percentage of task completed (0-100), used for partial ratings */
  completionPercentage?: number
  /** Reason for failure, used when rating is 'failure' */
  failureReason?: string
  /** ID of the last completed subtask, used for partial ratings */
  subtaskReached?: string
}

// ============================================================================
// Trajectory Quality Types
// ============================================================================

/** Quality score on a 1-5 scale */
export type QualityScore = 1 | 2 | 3 | 4 | 5

/** Flags indicating specific trajectory issues */
export type TrajectoryFlag =
  | 'jittery'
  | 'inefficient-path'
  | 'near-collision'
  | 'over-extension'
  | 'under-reaching'
  | 'hesitation'
  | 'correction-heavy'

/** Individual trajectory quality metrics */
export interface TrajectoryQualityMetrics {
  /** Jitter-free motion score */
  smoothness: QualityScore
  /** Path optimality score */
  efficiency: QualityScore
  /** Collision avoidance score */
  safety: QualityScore
  /** End-effector accuracy score */
  precision: QualityScore
}

/** Complete trajectory quality annotation */
export interface TrajectoryQualityAnnotation {
  /** Overall trajectory quality score */
  overallScore: QualityScore
  /** Individual metric scores */
  metrics: TrajectoryQualityMetrics
  /** Flags for specific issues observed */
  flags: TrajectoryFlag[]
}

// ============================================================================
// Data Quality Types
// ============================================================================

/** Overall data quality level */
export type DataQualityLevel = 'good' | 'acceptable' | 'poor' | 'unusable'

/** Types of data quality issues */
export type DataQualityIssueType =
  | 'frame-drop'
  | 'sync-issue'
  | 'occlusion'
  | 'lighting-issue'
  | 'sensor-noise'
  | 'calibration-drift'
  | 'encoding-artifact'
  | 'missing-data'

/** Severity of a data quality issue */
export type IssueSeverity = 'minor' | 'major' | 'critical'

/** Individual data quality issue */
export interface DataQualityIssue {
  /** Type of issue */
  type: DataQualityIssueType
  /** Severity of the issue */
  severity: IssueSeverity
  /** Frame range affected [start, end] */
  affectedFrames?: [number, number]
  /** Camera names or sensor IDs affected */
  affectedStreams?: string[]
  /** Additional notes about the issue */
  notes?: string
}

/** Complete data quality annotation */
export interface DataQualityAnnotation {
  /** Overall data quality assessment */
  overallQuality: DataQualityLevel
  /** List of specific issues found */
  issues: DataQualityIssue[]
}

// ============================================================================
// Anomaly Types
// ============================================================================

/** Severity of an anomaly */
export type AnomalySeverity = 'low' | 'medium' | 'high'

/** Types of anomalies that can be detected */
export type AnomalyType =
  | 'unexpected-stop'
  | 'trajectory-deviation'
  | 'force-spike'
  | 'velocity-spike'
  | 'object-slip'
  | 'gripper-failure'
  | 'collision'
  | 'other'

/** Individual anomaly marker */
export interface Anomaly {
  /** Unique identifier for this anomaly */
  id: string
  /** Type of anomaly */
  type: AnomalyType
  /** Severity level */
  severity: AnomalySeverity
  /** Frame range [start, end] */
  frameRange: [number, number]
  /** Timestamp range in seconds [start, end] */
  timestamp: [number, number]
  /** Human-readable description */
  description: string
  /** Whether this was auto-detected by the system */
  autoDetected: boolean
  /** Whether a human has verified this anomaly */
  verified: boolean
}

/** Container for anomaly annotations */
export interface AnomalyAnnotation {
  /** List of anomalies in this episode */
  anomalies: Anomaly[]
}

// ============================================================================
// Language Instruction Types (VLA)
// ============================================================================

/** Source of the language instruction */
export type InstructionSource = 'human' | 'template' | 'llm-generated' | 'retroactive'

/** Language instruction annotation for VLA-conditioned training */
export interface LanguageInstructionAnnotation {
  /** Primary task instruction */
  instruction: string
  /** How this instruction was produced */
  source: InstructionSource
  /** ISO 639-1 language code */
  language: string
  /** Alternative phrasings for data augmentation */
  paraphrases: string[]
  /** Ordered subtask decomposition */
  subtaskInstructions: string[]
}

// ============================================================================
// Combined Episode Annotation Types
// ============================================================================

/** Complete annotation for a single episode by one annotator */
export interface EpisodeAnnotation {
  /** Unique identifier for the annotator */
  annotatorId: string
  /** ISO 8601 timestamp when annotation was created/updated */
  timestamp: string
  /** Task completeness annotation */
  taskCompleteness: TaskCompletenessAnnotation
  /** Trajectory quality annotation */
  trajectoryQuality: TrajectoryQualityAnnotation
  /** Data quality annotation */
  dataQuality: DataQualityAnnotation
  /** Anomaly annotations */
  anomalies: AnomalyAnnotation
  /** Language instruction for VLA training */
  languageInstruction?: LanguageInstructionAnnotation
  /** Free-form notes about the episode */
  notes?: string
}

/** Consensus annotation derived from multiple annotators */
export interface EpisodeConsensus {
  /** Consensus task completeness rating */
  taskCompleteness: TaskCompletenessRating
  /** Average trajectory quality score */
  trajectoryScore: number
  /** Consensus data quality level */
  dataQuality: DataQualityLevel
  /** Inter-annotator agreement score (0-1) */
  agreementScore: number
}

/** Complete annotation file for an episode */
export interface EpisodeAnnotationFile {
  /** Schema version for this file format */
  schemaVersion: string
  /** Episode index within the dataset */
  episodeIndex: number
  /** Dataset identifier */
  datasetId: string
  /** List of annotations from different annotators */
  annotations: EpisodeAnnotation[]
  /** Computed consensus (if multiple annotators) */
  consensus?: EpisodeConsensus
}

// ============================================================================
// Default/Initial Values
// ============================================================================

/** Create a default task completeness annotation */
export function createDefaultTaskCompleteness(): TaskCompletenessAnnotation {
  return {
    rating: 'unknown',
    confidence: 3,
  }
}

/** Create a default trajectory quality annotation */
export function createDefaultTrajectoryQuality(): TrajectoryQualityAnnotation {
  return {
    overallScore: 3,
    metrics: {
      smoothness: 3,
      efficiency: 3,
      safety: 3,
      precision: 3,
    },
    flags: [],
  }
}

/** Create a default data quality annotation */
export function createDefaultDataQuality(): DataQualityAnnotation {
  return {
    overallQuality: 'good',
    issues: [],
  }
}

/** Create a default anomaly annotation */
export function createDefaultAnomalyAnnotation(): AnomalyAnnotation {
  return {
    anomalies: [],
  }
}

/** Create a default language instruction annotation */
export function createDefaultLanguageInstruction(): LanguageInstructionAnnotation {
  return {
    instruction: '',
    source: 'human',
    language: 'en',
    paraphrases: [],
    subtaskInstructions: [],
  }
}

/** Create a complete default episode annotation */
export function createDefaultEpisodeAnnotation(annotatorId: string): EpisodeAnnotation {
  return {
    annotatorId,
    timestamp: new Date().toISOString(),
    taskCompleteness: createDefaultTaskCompleteness(),
    trajectoryQuality: createDefaultTrajectoryQuality(),
    dataQuality: createDefaultDataQuality(),
    anomalies: createDefaultAnomalyAnnotation(),
  }
}
