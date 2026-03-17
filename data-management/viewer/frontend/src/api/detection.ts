/**
 * API client functions for YOLO11 object detection.
 */

import { handleResponse, mutationHeaders, requestHeaders } from '@/lib/api-client'
import type { DetectionRequest, EpisodeDetectionSummary } from '@/types/detection'

const API_BASE = '/api'

/**
 * Run YOLO11 object detection on episode frames.
 */
export async function runDetection(
  datasetId: string,
  episodeIdx: number,
  request: DetectionRequest = {},
): Promise<EpisodeDetectionSummary> {
  const response = await fetch(`${API_BASE}/datasets/${datasetId}/episodes/${episodeIdx}/detect`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(await mutationHeaders()),
    },
    body: JSON.stringify(request),
  })
  return handleResponse<EpisodeDetectionSummary>(response)
}

/**
 * Get cached detection results for an episode.
 */
export async function getDetections(
  datasetId: string,
  episodeIdx: number,
): Promise<EpisodeDetectionSummary | null> {
  const response = await fetch(
    `${API_BASE}/datasets/${datasetId}/episodes/${episodeIdx}/detections`,
    { headers: await requestHeaders() },
  )
  return handleResponse<EpisodeDetectionSummary | null>(response)
}

/**
 * Clear cached detection results for an episode.
 */
export async function clearDetections(
  datasetId: string,
  episodeIdx: number,
): Promise<{ cleared: boolean }> {
  const response = await fetch(
    `${API_BASE}/datasets/${datasetId}/episodes/${episodeIdx}/detections`,
    {
      method: 'DELETE',
      headers: await mutationHeaders(),
    },
  )
  return handleResponse<{ cleared: boolean }>(response)
}
