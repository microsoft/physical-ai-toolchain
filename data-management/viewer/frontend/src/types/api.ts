/**
 * API request and response type definitions.
 * These types define the contract between frontend and backend.
 */

import type {
  EpisodeAnnotation,
  EpisodeAnnotationFile,
  TaskCompletenessRating,
  TrajectoryFlag,
} from './annotations'

// ============================================================================
// Dataset Types
// ============================================================================

/** Schema definition for a dataset feature */
export interface FeatureSchema {
  /** Data type (e.g., 'float32', 'int64') */
  dtype: string
  /** Shape of the feature array */
  shape: number[]
}

/** Task definition within a dataset */
export interface TaskInfo {
  /** Task index identifier */
  taskIndex: number
  /** Human-readable task description */
  description: string
}

/** Complete dataset metadata */
export interface DatasetInfo {
  /** Unique dataset identifier */
  id: string
  /** Human-readable dataset name */
  name: string
  /** Total number of episodes in the dataset */
  totalEpisodes: number
  /** Frames per second */
  fps: number
  /** Feature schemas by feature name */
  features: Record<string, FeatureSchema>
  /** Available tasks in the dataset */
  tasks: TaskInfo[]
  /** Parent folder group for nested datasets */
  group?: string | null
}

/** Capabilities available for a dataset */
export interface DatasetCapabilities {
  /** Whether h5py is installed and available on backend */
  hdf5Support: boolean
  /** Whether this dataset has HDF5 episode files */
  hasHdf5Files: boolean
  /** Whether pyarrow is installed and available on backend */
  lerobotSupport: boolean
  /** Whether this dataset is in LeRobot parquet format */
  isLerobotDataset: boolean
  /** Number of episodes detected */
  episodeCount: number
}

// ============================================================================
// Episode Types
// ============================================================================

/** Episode metadata for list views */
export interface EpisodeMeta {
  /** Episode index within the dataset */
  index: number
  /** Episode identifier */
  id?: string
  /** Number of frames in the episode */
  length: number
  /** Task index this episode belongs to */
  taskIndex: number
  /** Task description */
  task?: string
  /** Whether this episode has any annotations */
  hasAnnotations: boolean
  /** Annotation status for batch views */
  annotationStatus?: 'pending' | 'in-progress' | 'complete'
  /** Thumbnail URL for preview */
  thumbnailUrl?: string
}

/** Single trajectory data point */
export interface TrajectoryPoint {
  /** Timestamp in seconds */
  timestamp: number
  /** Frame index */
  frame: number
  /** Joint positions array */
  jointPositions: number[]
  /** Joint velocities array */
  jointVelocities: number[]
  /** End-effector pose (position + orientation) */
  endEffectorPose: number[]
  /** Gripper state (0 = open, 1 = closed) */
  gripperState: number
}

/** Complete episode data for viewing */
export interface EpisodeData {
  /** Episode metadata */
  meta: EpisodeMeta
  /** Video URLs by camera name */
  videoUrls: Record<string, string>
  /** Video time offset in seconds for this episode within a concatenated video */
  videoStartTime?: number
  /** Available camera names */
  cameras: string[]
  /** Trajectory data points */
  trajectoryData: TrajectoryPoint[]
}

// ============================================================================
// Auto-Analysis Types
// ============================================================================

/** Computed trajectory quality metrics from auto-analysis */
export interface ComputedQualityMetrics {
  /** Smoothness score (0-1) */
  smoothnessScore: number
  /** Efficiency score (0-1) */
  efficiencyScore: number
  /** Jitter metric (lower is better) */
  jitterMetric: number
  /** Number of hesitation events detected */
  hesitationCount: number
  /** Number of correction events detected */
  correctionCount: number
}

/** Auto-analysis result for an episode */
export interface AutoQualityAnalysis {
  /** Episode index that was analyzed */
  episodeIndex: number
  /** Computed metrics */
  computed: ComputedQualityMetrics
  /** Suggested overall rating (1-5) */
  suggestedRating: 1 | 2 | 3 | 4 | 5
  /** Confidence in the suggestion (0-1) */
  confidence: number
  /** Suggested trajectory flags */
  flags: TrajectoryFlag[]
}

// ============================================================================
// Annotation Summary Types
// ============================================================================

/** Aggregated annotation metrics for a dataset */
export interface AnnotationSummary {
  /** Dataset identifier */
  datasetId: string
  /** Total episodes in dataset */
  totalEpisodes: number
  /** Number of annotated episodes */
  annotatedEpisodes: number
  /** Distribution of task completeness ratings */
  taskCompletenessDistribution: Record<TaskCompletenessRating, number>
  /** Distribution of quality scores (1-5) */
  qualityScoreDistribution: Record<number, number>
  /** Counts of each anomaly type */
  anomalyTypeCounts: Record<string, number>
}

// ============================================================================
// Curriculum Types
// ============================================================================

/** Criteria for filtering episodes into a curriculum stage */
export interface CurriculumCriteria {
  /** Minimum quality score to include */
  minQualityScore?: number
  /** Task completeness ratings to include */
  taskCompleteness?: TaskCompletenessRating[]
  /** Trajectory flags to exclude */
  excludeFlags?: TrajectoryFlag[]
  /** Maximum number of anomalies allowed */
  maxAnomalyCount?: number
}

/** Single stage in a training curriculum */
export interface CurriculumStage {
  /** Stage name */
  name: string
  /** Episode indices in this stage */
  episodeIndices: number[]
  /** Criteria used to select episodes */
  criteria: CurriculumCriteria
}

/** Curriculum ordering strategy */
export type CurriculumStrategy = 'difficulty-ascending' | 'quality-descending' | 'balanced'

/** Complete curriculum definition */
export interface CurriculumDefinition {
  /** Curriculum name */
  name: string
  /** Ordering strategy */
  strategy: CurriculumStrategy
  /** Curriculum stages */
  stages: CurriculumStage[]
}

// ============================================================================
// API Request/Response Types
// ============================================================================

/** Request to save an annotation */
export interface SaveAnnotationRequest {
  /** Dataset ID */
  datasetId: string
  /** Episode index */
  episodeIndex: number
  /** Annotation to save */
  annotation: EpisodeAnnotation
}

/** Response from save annotation */
export interface SaveAnnotationResponse {
  /** Success status */
  success: boolean
  /** Updated annotation file */
  annotationFile: EpisodeAnnotationFile
}

/** Request to trigger auto-analysis */
export interface AutoAnalysisRequest {
  /** Dataset ID */
  datasetId: string
  /** Episode index */
  episodeIndex: number
}

/** Request to generate a curriculum */
export interface GenerateCurriculumRequest {
  /** Dataset ID */
  datasetId: string
  /** Curriculum definition */
  curriculum: CurriculumDefinition
}

/** Curriculum export format */
export type ExportFormat = 'json' | 'indices' | 'parquet'

/** Request to export a curriculum */
export interface ExportCurriculumRequest {
  /** Dataset ID */
  datasetId: string
  /** Curriculum to export */
  curriculum: CurriculumDefinition
  /** Export format */
  format: ExportFormat
}

// ============================================================================
// Clustering Types
// ============================================================================

/** Similarity level for episode clusters */
export type SimilarityLevel = 'high' | 'medium' | 'low'

/** Episode cluster from similarity analysis */
export interface EpisodeCluster {
  /** Cluster identifier */
  id: string
  /** Index of the centroid episode */
  centroidEpisode: number
  /** Episode indices in this cluster */
  members: number[]
  /** Similarity level within cluster */
  similarity: SimilarityLevel
  /** Suggested label if cluster has consensus */
  suggestedLabel?: string
}

/** Clustering results for a dataset */
export interface EpisodeClustering {
  /** List of clusters */
  clusters: EpisodeCluster[]
}

// ============================================================================
// Error Types
// ============================================================================

/** API error response */
export interface ApiError {
  /** Error code */
  code: string
  /** Human-readable message */
  message: string
  /** Additional details */
  details?: Record<string, unknown>
}
