/**
 * API client for robotic training data annotation backend.
 *
 * Provides type-safe API calls with error handling.
 */

import type {
  AnnotationSummary,
  ApiError,
  AutoQualityAnalysis,
  DatasetCapabilities,
  DatasetInfo,
  EpisodeAnnotation,
  EpisodeAnnotationFile,
  EpisodeData,
  EpisodeMeta,
} from '@/types'

import { getAuthHeaders } from './auth-headers'

const API_BASE = '/api'

/** Cached CSRF token fetched from the server. */
let _csrfToken: string | null = null
/** In-flight CSRF token fetch promise to prevent duplicate requests. */
let _csrfTokenFetch: Promise<string> | null = null

async function getCsrfToken(): Promise<string> {
  if (_csrfToken) return _csrfToken
  if (!_csrfTokenFetch) {
    _csrfTokenFetch = fetch(`${API_BASE}/csrf-token`)
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Failed to fetch CSRF token: ${response.statusText}`)
        }
        return response.json()
      })
      .then((data) => {
        _csrfToken = data.csrf_token as string
        _csrfTokenFetch = null
        return _csrfToken
      })
      .catch((err) => {
        _csrfTokenFetch = null
        throw err
      })
  }
  return _csrfTokenFetch
}

export async function requestHeaders(): Promise<Record<string, string>> {
  return { ...(await getAuthHeaders()) }
}

export async function mutationHeaders(): Promise<Record<string, string>> {
  return { 'X-CSRF-Token': await getCsrfToken(), ...(await getAuthHeaders()) }
}

/** Reset cached CSRF token (for testing). */
export function _resetCsrfToken(): void {
  _csrfToken = null
  _csrfTokenFetch = null
}

/**
 * Convert snake_case keys to camelCase recursively.
 */
function snakeToCamel(str: string): string {
  return str.replace(/_([a-z])/g, (_, letter) => letter.toUpperCase())
}

function transformKeys<T>(obj: unknown): T {
  if (Array.isArray(obj)) {
    return obj.map(transformKeys) as T
  }
  if (obj !== null && typeof obj === 'object') {
    return Object.fromEntries(
      Object.entries(obj as Record<string, unknown>).map(([key, value]) => [
        snakeToCamel(key),
        transformKeys(value),
      ]),
    ) as T
  }
  return obj as T
}

/**
 * Custom error class for API errors.
 */
export class ApiClientError extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly status: number,
    public readonly details?: Record<string, unknown>,
  ) {
    super(message)
    this.name = 'ApiClientError'
  }
}

/**
 * Handle API response, throwing on error.
 */
export async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let error: ApiError
    try {
      error = await response.json()
    } catch {
      error = {
        code: 'UNKNOWN_ERROR',
        message: response.statusText || 'An unknown error occurred',
      }
    }

    throw new ApiClientError(error.message, error.code, response.status, error.details)
  }

  return response.json()
}

// ============================================================================
// Dataset API
// ============================================================================

/**
 * Fetch all available datasets.
 */
export async function fetchDatasets(): Promise<DatasetInfo[]> {
  const response = await fetch(`${API_BASE}/datasets`, {
    headers: await requestHeaders(),
  })
  return handleResponse<DatasetInfo[]>(response)
}

/**
 * Fetch a specific dataset by ID.
 */
export async function fetchDataset(datasetId: string): Promise<DatasetInfo> {
  const response = await fetch(`${API_BASE}/datasets/${datasetId}`, {
    headers: await requestHeaders(),
  })
  return handleResponse<DatasetInfo>(response)
}

/**
 * Fetch capabilities for a dataset.
 */
export async function fetchCapabilities(datasetId: string): Promise<DatasetCapabilities> {
  const response = await fetch(`${API_BASE}/datasets/${datasetId}/capabilities`, {
    headers: await requestHeaders(),
  })
  const data = await handleResponse<unknown>(response)
  return transformKeys<DatasetCapabilities>(data)
}

/**
 * Fetch episodes for a dataset with optional filtering.
 */
export async function fetchEpisodes(
  datasetId: string,
  options?: {
    offset?: number
    limit?: number
    hasAnnotations?: boolean
    taskIndex?: number
  },
): Promise<EpisodeMeta[]> {
  const params = new URLSearchParams()

  if (options?.offset !== undefined) {
    params.set('offset', options.offset.toString())
  }
  if (options?.limit !== undefined) {
    params.set('limit', options.limit.toString())
  }
  if (options?.hasAnnotations !== undefined) {
    params.set('has_annotations', options.hasAnnotations.toString())
  }
  if (options?.taskIndex !== undefined) {
    params.set('task_index', options.taskIndex.toString())
  }

  const query = params.toString()
  const url = `${API_BASE}/datasets/${datasetId}/episodes${query ? `?${query}` : ''}`

  const response = await fetch(url, {
    headers: await requestHeaders(),
  })
  const data = await handleResponse<unknown>(response)
  return transformKeys<EpisodeMeta[]>(data)
}

/**
 * Fetch a specific episode by index.
 */
export async function fetchEpisode(datasetId: string, episodeIndex: number): Promise<EpisodeData> {
  const response = await fetch(`${API_BASE}/datasets/${datasetId}/episodes/${episodeIndex}`, {
    headers: await requestHeaders(),
  })
  const data = await handleResponse<unknown>(response)
  return transformKeys<EpisodeData>(data)
}

// ============================================================================
// Annotation API
// ============================================================================

/**
 * Fetch annotations for an episode.
 */
export async function fetchAnnotations(
  datasetId: string,
  episodeIndex: number,
): Promise<EpisodeAnnotationFile> {
  const response = await fetch(
    `${API_BASE}/datasets/${datasetId}/episodes/${episodeIndex}/annotations`,
    { headers: await requestHeaders() },
  )
  return handleResponse<EpisodeAnnotationFile>(response)
}

/**
 * Save an annotation for an episode.
 */
export async function saveAnnotation(
  datasetId: string,
  episodeIndex: number,
  annotation: EpisodeAnnotation,
): Promise<EpisodeAnnotationFile> {
  const response = await fetch(
    `${API_BASE}/datasets/${datasetId}/episodes/${episodeIndex}/annotations`,
    {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        ...(await mutationHeaders()),
      },
      body: JSON.stringify(annotation),
    },
  )
  return handleResponse<EpisodeAnnotationFile>(response)
}

/**
 * Delete annotations for an episode.
 */
export async function deleteAnnotations(
  datasetId: string,
  episodeIndex: number,
  annotatorId?: string,
): Promise<{ deleted: boolean; episodeIndex: number }> {
  const params = annotatorId ? `?annotator_id=${annotatorId}` : ''
  const response = await fetch(
    `${API_BASE}/datasets/${datasetId}/episodes/${episodeIndex}/annotations${params}`,
    {
      method: 'DELETE',
      headers: await mutationHeaders(),
    },
  )
  return handleResponse(response)
}

/**
 * Trigger auto-analysis for an episode.
 */
export async function triggerAutoAnalysis(
  datasetId: string,
  episodeIndex: number,
): Promise<AutoQualityAnalysis> {
  const response = await fetch(
    `${API_BASE}/datasets/${datasetId}/episodes/${episodeIndex}/annotations/auto`,
    {
      method: 'POST',
      headers: await mutationHeaders(),
    },
  )
  return handleResponse<AutoQualityAnalysis>(response)
}

/**
 * Fetch annotation summary for a dataset.
 */
export async function fetchAnnotationSummary(datasetId: string): Promise<AnnotationSummary> {
  const response = await fetch(`${API_BASE}/datasets/${datasetId}/annotations/summary`, {
    headers: await requestHeaders(),
  })
  return handleResponse<AnnotationSummary>(response)
}

// ============================================================================
// Cache Stats API
// ============================================================================

export interface CacheStats {
  capacity: number
  size: number
  hits: number
  misses: number
  hitRate: number
  totalBytes: number
  maxMemoryBytes: number
}

/**
 * Fetch episode cache performance metrics.
 */
export async function fetchCacheStats(): Promise<CacheStats> {
  const response = await fetch(`${API_BASE}/datasets/cache/stats`, {
    headers: await requestHeaders(),
  })
  const data = await handleResponse<unknown>(response)
  return transformKeys<CacheStats>(data)
}

/**
 * Warm the episode cache for a dataset by preloading the first N episodes.
 */
export async function warmCache(datasetId: string, count = 5): Promise<void> {
  await fetch(`${API_BASE}/datasets/${datasetId}/cache/warm?count=${count}`, {
    method: 'POST',
    headers: await mutationHeaders(),
  })
}
