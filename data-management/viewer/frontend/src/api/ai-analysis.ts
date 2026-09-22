/**
 * API client for AI analysis endpoints.
 */

import { apiRequest } from '@/lib/api-client'

/** Smoothness normalization mode for normalized_smoothness. */
export type SmoothnessMode = 'log-scaled' | 'radian-based'

/** Trajectory data for analysis */
export interface TrajectoryData {
  positions: number[][]
  timestamps: number[]
  gripperStates?: number[]
  /** Normalization for normalized_smoothness; defaults to 'log-scaled' on the backend. */
  smoothnessMode?: SmoothnessMode
}

/** Trajectory metrics response */
export interface TrajectoryMetrics {
  smoothness: number
  /** Rescaled smoothness (0-1) that discriminates across degree-scale episodes. */
  normalizedSmoothness: number
  efficiency: number
  jitter: number
  hesitationCount: number
  correctionCount: number
  overallScore: number
  flags: string[]
}

/** Detected anomaly */
export interface DetectedAnomaly {
  id: string
  type: string
  severity: 'low' | 'medium' | 'high'
  frameStart: number
  frameEnd: number
  description: string
  confidence: number
  autoDetected: boolean
}

/** Anomaly detection request */
export interface AnomalyDetectionRequest {
  positions: number[][]
  timestamps: number[]
  forces?: number[][]
  gripperStates?: number[]
  gripperCommands?: number[]
}

/** Anomaly detection response */
export interface AnomalyDetectionResponse {
  anomalies: DetectedAnomaly[]
  totalCount: number
  severityCounts: Record<string, number>
}

/** Cluster assignment */
export interface ClusterAssignment {
  episodeIndex: number
  clusterId: number
  similarityScore: number
}

/** Clustering request */
export interface ClusterRequest {
  trajectories: number[][][]
  numClusters?: number
}

/** Clustering response */
export interface ClusterResponse {
  numClusters: number
  assignments: ClusterAssignment[]
  clusterSizes: Record<string, number>
  silhouetteScore: number
}

/** Annotation suggestion request */
export interface SuggestAnnotationRequest {
  positions: number[][]
  timestamps: number[]
  gripperStates?: number[]
  forces?: number[][]
}

/** AI annotation suggestion */
export interface AnnotationSuggestion {
  taskCompletionRating: number
  trajectoryQualityScore: number
  suggestedFlags: string[]
  detectedAnomalies: DetectedAnomaly[]
  confidence: number
  reasoning: string
}

/**
 * Analyze trajectory quality.
 */
export async function analyzeTrajectory(data: TrajectoryData): Promise<TrajectoryMetrics> {
  return apiRequest<TrajectoryMetrics>('/ai/trajectory-analysis', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      positions: data.positions,
      timestamps: data.timestamps,
      gripper_states: data.gripperStates,
      smoothness_mode: data.smoothnessMode,
    }),
  })
}

/**
 * Detect anomalies in a trajectory.
 */
export async function detectAnomalies(
  request: AnomalyDetectionRequest,
): Promise<AnomalyDetectionResponse> {
  return apiRequest<AnomalyDetectionResponse>('/ai/anomaly-detection', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      positions: request.positions,
      timestamps: request.timestamps,
      forces: request.forces,
      gripper_states: request.gripperStates,
      gripper_commands: request.gripperCommands,
    }),
  })
}

/**
 * Cluster episodes by trajectory similarity.
 */
export async function clusterEpisodes(request: ClusterRequest): Promise<ClusterResponse> {
  return apiRequest<ClusterResponse>('/ai/cluster', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      trajectories: request.trajectories,
      num_clusters: request.numClusters,
    }),
  })
}

/**
 * Get AI annotation suggestions for an episode.
 */
export async function getAnnotationSuggestion(
  request: SuggestAnnotationRequest,
): Promise<AnnotationSuggestion> {
  return apiRequest<AnnotationSuggestion>('/ai/suggest-annotation', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      positions: request.positions,
      timestamps: request.timestamps,
      gripper_states: request.gripperStates,
      forces: request.forces,
    }),
  })
}
