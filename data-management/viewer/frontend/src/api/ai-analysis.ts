/**
 * API client for AI analysis endpoints.
 */

import { handleResponse, mutationHeaders } from '@/lib/api-client'

const API_BASE = '/api'

/** Trajectory data for analysis */
export interface TrajectoryData {
  positions: number[][]
  timestamps: number[]
  gripper_states?: number[]
}

/** Trajectory metrics response */
export interface TrajectoryMetrics {
  smoothness: number
  efficiency: number
  jitter: number
  hesitation_count: number
  correction_count: number
  overall_score: number
  flags: string[]
}

/** Detected anomaly */
export interface DetectedAnomaly {
  id: string
  type: string
  severity: 'low' | 'medium' | 'high'
  frame_start: number
  frame_end: number
  description: string
  confidence: number
  auto_detected: boolean
}

/** Anomaly detection request */
export interface AnomalyDetectionRequest {
  positions: number[][]
  timestamps: number[]
  forces?: number[][]
  gripper_states?: number[]
  gripper_commands?: number[]
}

/** Anomaly detection response */
export interface AnomalyDetectionResponse {
  anomalies: DetectedAnomaly[]
  total_count: number
  severity_counts: Record<string, number>
}

/** Cluster assignment */
export interface ClusterAssignment {
  episode_index: number
  cluster_id: number
  similarity_score: number
}

/** Clustering request */
export interface ClusterRequest {
  trajectories: number[][][]
  num_clusters?: number
}

/** Clustering response */
export interface ClusterResponse {
  num_clusters: number
  assignments: ClusterAssignment[]
  cluster_sizes: Record<string, number>
  silhouette_score: number
}

/** Annotation suggestion request */
export interface SuggestAnnotationRequest {
  positions: number[][]
  timestamps: number[]
  gripper_states?: number[]
  forces?: number[][]
}

/** AI annotation suggestion */
export interface AnnotationSuggestion {
  task_completion_rating: number
  trajectory_quality_score: number
  suggested_flags: string[]
  detected_anomalies: DetectedAnomaly[]
  confidence: number
  reasoning: string
}

/**
 * Analyze trajectory quality.
 */
export async function analyzeTrajectory(data: TrajectoryData): Promise<TrajectoryMetrics> {
  const response = await fetch(`${API_BASE}/ai/trajectory-analysis`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(await mutationHeaders()),
    },
    body: JSON.stringify(data),
  })
  return handleResponse<TrajectoryMetrics>(response)
}

/**
 * Detect anomalies in a trajectory.
 */
export async function detectAnomalies(
  request: AnomalyDetectionRequest,
): Promise<AnomalyDetectionResponse> {
  const response = await fetch(`${API_BASE}/ai/anomaly-detection`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(await mutationHeaders()),
    },
    body: JSON.stringify(request),
  })
  return handleResponse<AnomalyDetectionResponse>(response)
}

/**
 * Cluster episodes by trajectory similarity.
 */
export async function clusterEpisodes(request: ClusterRequest): Promise<ClusterResponse> {
  const response = await fetch(`${API_BASE}/ai/cluster`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(await mutationHeaders()),
    },
    body: JSON.stringify(request),
  })
  return handleResponse<ClusterResponse>(response)
}

/**
 * Get AI annotation suggestions for an episode.
 */
export async function getAnnotationSuggestion(
  request: SuggestAnnotationRequest,
): Promise<AnnotationSuggestion> {
  const response = await fetch(`${API_BASE}/ai/suggest-annotation`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(await mutationHeaders()),
    },
    body: JSON.stringify(request),
  })
  return handleResponse<AnnotationSuggestion>(response)
}
