import { apiRequest } from '@/lib/api-client'
import type {
  AnnotationRevision,
  EditRevision,
  QualityReport,
  QualityRunRequest,
  ReviewDecision,
} from '@/types'

function reviewPath(datasetId: string, episodeIndex: number, resource: string): string {
  return `/datasets/${datasetId}/episodes/${episodeIndex}/review/${resource}`
}

function snakeCaseKey(key: string): string {
  return key.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`)
}

function serializeReviewContract(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(serializeReviewContract)
  }
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, nestedValue]) => [
        snakeCaseKey(key),
        serializeReviewContract(nestedValue),
      ]),
    )
  }
  return value
}

function postReview<T>(path: string, body: unknown): Promise<T> {
  return apiRequest<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(serializeReviewContract(body)),
  })
}

export function runQualityReview(
  datasetId: string,
  episodeIndex: number,
  request: QualityRunRequest,
): Promise<QualityReport> {
  return postReview(reviewPath(datasetId, episodeIndex, 'quality-runs'), request)
}

export function fetchLatestReviewQuality(
  datasetId: string,
  episodeIndex: number,
): Promise<QualityReport | null> {
  return apiRequest<QualityReport | null>(
    reviewPath(datasetId, episodeIndex, 'quality-reports/latest'),
  )
}

export function fetchLatestReviewDecision(
  datasetId: string,
  episodeIndex: number,
): Promise<ReviewDecision | null> {
  return apiRequest<ReviewDecision | null>(reviewPath(datasetId, episodeIndex, 'decisions/latest'))
}

export function createAnnotationRevision(
  datasetId: string,
  episodeIndex: number,
  revision: AnnotationRevision,
): Promise<AnnotationRevision> {
  return postReview(reviewPath(datasetId, episodeIndex, 'annotation-revisions'), revision)
}

export function createEditRevision(
  datasetId: string,
  episodeIndex: number,
  revision: EditRevision,
): Promise<EditRevision> {
  return postReview(reviewPath(datasetId, episodeIndex, 'edit-revisions'), revision)
}

export function createReviewDecision(
  datasetId: string,
  episodeIndex: number,
  decision: ReviewDecision,
): Promise<ReviewDecision> {
  return postReview(reviewPath(datasetId, episodeIndex, 'decisions'), decision)
}
