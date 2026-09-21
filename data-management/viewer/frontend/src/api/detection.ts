/**
 * API client functions for YOLO11 object detection.
 */

import { apiRequest, transformKeys } from '@/lib/api-client'
import type { DetectionRequest, EpisodeDetectionSummary } from '@/types/detection'

function transformDetectionSummary(data: unknown): EpisodeDetectionSummary {
  const raw = data as Record<string, unknown>
  const summary = transformKeys<EpisodeDetectionSummary>(raw)
  const rawClassSummary = raw.class_summary

  if (rawClassSummary && typeof rawClassSummary === 'object') {
    summary.classSummary = Object.fromEntries(
      Object.entries(rawClassSummary).map(([className, value]) => [
        className,
        transformKeys(value),
      ]),
    )
  } else if (rawClassSummary == null) {
    summary.classSummary = {}
  }

  return summary
}

function transformOptionalDetectionSummary(data: unknown): EpisodeDetectionSummary | null {
  return data === null ? null : transformDetectionSummary(data)
}

/**
 * Run YOLO11 object detection on episode frames.
 */
export async function runDetection(
  datasetId: string,
  episodeIdx: number,
  request: DetectionRequest = {},
): Promise<EpisodeDetectionSummary> {
  return apiRequest<EpisodeDetectionSummary>(
    `/datasets/${datasetId}/episodes/${episodeIdx}/detect`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    },
    transformDetectionSummary,
  )
}

/**
 * Get cached detection results for an episode.
 */
export async function getDetections(
  datasetId: string,
  episodeIdx: number,
): Promise<EpisodeDetectionSummary | null> {
  return apiRequest<EpisodeDetectionSummary | null>(
    `/datasets/${datasetId}/episodes/${episodeIdx}/detections`,
    {},
    transformOptionalDetectionSummary,
  )
}

/**
 * Clear cached detection results for an episode.
 */
export async function clearDetections(
  datasetId: string,
  episodeIdx: number,
): Promise<{ cleared: boolean }> {
  return apiRequest<{ cleared: boolean }>(
    `/datasets/${datasetId}/episodes/${episodeIdx}/detections`,
    { method: 'DELETE' },
  )
}
