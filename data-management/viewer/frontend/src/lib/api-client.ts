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
  VlmJudgeResult,
  VlmJudgeRunOptions,
  VlmJudgeStatus,
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

/** Fetch wrapper that attaches CSRF + auth headers; caller headers win on key collision. */
export async function mutationFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const method = (init.method ?? 'GET').toUpperCase()
  const needsCsrf = method !== 'GET' && method !== 'HEAD'
  const baseHeaders = needsCsrf ? await mutationHeaders() : await requestHeaders()
  return fetch(input, {
    ...init,
    headers: { ...baseHeaders, ...(init.headers ?? {}) },
  })
}

/** Reset cached CSRF token (for testing). */
export function _resetCsrfToken(): void {
  _csrfToken = null
  _csrfTokenFetch = null
}

/**
 * Convert snake_case keys to camelCase recursively.
 */
export function snakeToCamel(str: string): string {
  return str.replace(/_([a-z])/g, (_, letter) => letter.toUpperCase())
}

export function transformKeys<T>(obj: unknown): T {
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
 * Apply transformKeys to a dataset payload while preserving the original
 * `features` map keys (camera/feature names like ``observation.images.front``
 * must not be camelCased).
 */
function preserveDatasetFeatureKeys(raw: Record<string, unknown>): DatasetInfo {
  const originalFeatures = raw.features as Record<string, unknown> | undefined
  const dataset = transformKeys<DatasetInfo>(raw)
  if (originalFeatures) {
    dataset.features = Object.fromEntries(
      Object.entries(originalFeatures).map(([key, value]) => [key, transformKeys(value)]),
    ) as DatasetInfo['features']
  }
  return dataset
}

/**
 * Apply transformKeys to an episode payload while preserving the original
 * trajectory variable map keys (e.g. ``observation.gripper.is_closed``) which
 * must not be camelCased so frontend lookups via ``trajectoryVariables[i].key``
 * remain valid. Also preserves the ``video_urls`` map keys so they match the
 * raw camera names returned in ``cameras`` (e.g. ``observation.images.cam_0``).
 */
function preserveEpisodeVariableKeys(raw: Record<string, unknown>): EpisodeData {
  const rawTrajectory = raw.trajectory_data as Array<Record<string, unknown>> | undefined
  const rawVideoUrls = raw.video_urls as Record<string, unknown> | undefined
  const rawVideoTimeWindows = raw.video_time_windows as Record<string, unknown> | undefined
  const episode = transformKeys<EpisodeData>(raw)
  if (rawTrajectory && Array.isArray(episode.trajectoryData)) {
    episode.trajectoryData = episode.trajectoryData.map((frame, frameIndex) => {
      const originalVariables = rawTrajectory[frameIndex]?.variables as
        Record<string, unknown> | undefined
      if (!originalVariables) {
        return frame
      }
      return { ...frame, variables: { ...(originalVariables as Record<string, number>) } }
    })
  }
  if (rawVideoUrls) {
    episode.videoUrls = { ...(rawVideoUrls as Record<string, string>) }
  }
  if (rawVideoTimeWindows) {
    episode.videoTimeWindows = { ...(rawVideoTimeWindows as Record<string, [number, number]>) }
  }
  return episode
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
  const raw = await handleResponse<Array<Record<string, unknown>>>(response)
  return raw.map(preserveDatasetFeatureKeys)
}

/**
 * Fetch a specific dataset by ID.
 */
export async function fetchDataset(datasetId: string): Promise<DatasetInfo> {
  const response = await fetch(`${API_BASE}/datasets/${datasetId}`, {
    headers: await requestHeaders(),
  })
  const raw = await handleResponse<Record<string, unknown>>(response)
  return preserveDatasetFeatureKeys(raw)
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
  const raw = await handleResponse<Record<string, unknown>>(response)
  return preserveEpisodeVariableKeys(raw)
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

// ============================================================================
// VLM-as-Judge API
// ============================================================================

/** Status returned to the panel when the judge router is not mounted. */
const VLM_JUDGE_DISABLED: VlmJudgeStatus = {
  enabled: false,
  cached: false,
  jobStatus: 'idle',
  judgeModel: null,
  promptVersion: null,
  cacheKey: null,
  error: null,
  result: null,
}

const VLM_JUDGE_POLL_INTERVAL_MS = 1000
const VLM_JUDGE_POLL_TIMEOUT_MS = 30 * 60_000

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, ms))
}

/**
 * Fetch any cached VLM-judge result for an episode without running inference.
 *
 * Returns ``{enabled: false}`` when the backend has not been configured with
 * ``VLM_JUDGE_ENABLED=true`` — in that case the router is not mounted and the
 * endpoint responds with 404, which we map to the disabled status so the panel
 * hides cleanly instead of surfacing a spurious error.
 */
export async function fetchVlmJudgeStatus(
  datasetId: string,
  episodeIndex: number,
  options: { cacheKey?: string | null } = {},
): Promise<VlmJudgeStatus> {
  const params = new URLSearchParams()
  if (options.cacheKey) params.set('cache_key', options.cacheKey)
  const query = params.size > 0 ? `?${params.toString()}` : ''
  const response = await fetch(
    `${API_BASE}/datasets/${datasetId}/episodes/${episodeIndex}/judge${query}`,
    {
      headers: await requestHeaders(),
    },
  )
  if (response.status === 404) return VLM_JUDGE_DISABLED
  const data = await handleResponse<unknown>(response)
  return transformKeys<VlmJudgeStatus>(data)
}

/**
 * Run the VLM judge on an episode (cache-first unless ``force`` is true).
 */
export async function runVlmJudge(
  datasetId: string,
  episodeIndex: number,
  options: VlmJudgeRunOptions = {},
): Promise<VlmJudgeResult> {
  const response = await fetch(`${API_BASE}/datasets/${datasetId}/episodes/${episodeIndex}/judge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await mutationHeaders()) },
    body: JSON.stringify({
      instruction: options.instruction,
      views: options.views,
      process_method: options.processMethod,
      force: options.force ?? false,
    }),
  })
  const data = transformKeys<VlmJudgeStatus | VlmJudgeResult>(
    await handleResponse<unknown>(response),
  )
  if ('episodeId' in data) return data
  if (data.result) return data.result
  if (!data.cacheKey) {
    throw new ApiClientError(
      'VLM judge did not return a pollable cache key',
      'VLM_JUDGE_NO_CACHE_KEY',
      500,
    )
  }

  const started = Date.now()
  while (Date.now() - started < VLM_JUDGE_POLL_TIMEOUT_MS) {
    await sleep(VLM_JUDGE_POLL_INTERVAL_MS)
    const status = await fetchVlmJudgeStatus(datasetId, episodeIndex, { cacheKey: data.cacheKey })
    if (status.jobStatus === 'error') {
      throw new ApiClientError(status.error ?? 'VLM judge failed', 'VLM_JUDGE_FAILED', 502)
    }
    if (status.result) return status.result
  }

  throw new ApiClientError(
    'VLM judge timed out while waiting for a result',
    'VLM_JUDGE_TIMEOUT',
    408,
  )
}

// ============================================================================
// Episode Labels API
// ============================================================================

export interface EpisodeLabelsResult {
  episodeIndex: number
  labels: string[]
}

/**
 * Replace the label set assigned to a single episode.
 */
export async function setEpisodeLabels(
  datasetId: string,
  episodeIndex: number,
  labels: string[],
): Promise<EpisodeLabelsResult> {
  const response = await mutationFetch(
    `${API_BASE}/datasets/${datasetId}/episodes/${episodeIndex}/labels`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ labels }),
    },
  )
  const data = await handleResponse<unknown>(response)
  return transformKeys<EpisodeLabelsResult>(data)
}
