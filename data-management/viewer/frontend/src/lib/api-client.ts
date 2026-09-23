/**
 * API client for robotic training data annotation backend.
 *
 * Provides type-safe API calls with error handling.
 */

import type {
  AnnotationSummary,
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

export const API_BASE = '/api'

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
  const headers = new Headers(baseHeaders)
  new Headers(init.headers).forEach((value, name) => headers.set(name, value))
  return fetch(input, {
    ...init,
    headers: Object.fromEntries(headers.entries()),
  })
}

export function apiPath(path: string): string {
  return `${API_BASE}/${path.replace(/^\/+/, '')}`
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  return mutationFetch(apiPath(path), init)
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

export interface VersionedResource<T> {
  data: T
  etag: string | null
}

export interface MutationPrecondition {
  etag?: string
  createOnly?: boolean
}

export function mutationPreconditionHeaders(
  precondition: MutationPrecondition,
): Record<string, string> {
  if (precondition.etag && precondition.createOnly) {
    throw new Error('Specify only one mutation precondition')
  }
  if (precondition.etag) return { 'If-Match': precondition.etag }
  if (precondition.createOnly) return { 'If-None-Match': '*' }
  throw new Error('A mutation precondition is required')
}

function publicErrorMessage(status: number): string {
  if (status >= 500) {
    return 'The server could not complete the request'
  }

  switch (status) {
    case 400:
      return 'The request is invalid'
    case 401:
      return 'Authentication is required'
    case 403:
      return 'You do not have permission to perform this action'
    case 404:
      return 'The requested resource was not found'
    case 409:
      return 'The request conflicts with the current state'
    case 413:
      return 'The request is too large'
    case 422:
      return 'The request contains invalid data'
    case 429:
      return 'Too many requests; try again later'
    default:
      return 'The request could not be completed'
  }
}

/**
 * Handle API response, throwing on error.
 */
export async function handleResponse<T>(
  response: Response,
  transform: (data: unknown) => T = transformKeys<T>,
): Promise<T> {
  if (!response.ok) {
    let code = `HTTP_${response.status}`
    let details: Record<string, unknown> | undefined
    try {
      const payload: unknown = await response.json()
      if (payload !== null && typeof payload === 'object') {
        if ('code' in payload && typeof payload.code === 'string') {
          code = payload.code
        }
        if (
          response.status === 412 &&
          'details' in payload &&
          payload.details !== null &&
          typeof payload.details === 'object' &&
          !Array.isArray(payload.details)
        ) {
          details = transformKeys<Record<string, unknown>>(payload.details)
        }
      }
    } catch {
      // Non-JSON errors still surface through the status-derived public error.
    }

    throw new ApiClientError(publicErrorMessage(response.status), code, response.status, details)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return transform(await response.json())
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
  transform?: (data: unknown) => T,
): Promise<T> {
  const response = await apiFetch(path, init)
  return transform ? handleResponse(response, transform) : handleResponse<T>(response)
}

export async function apiRequestVersioned<T>(
  path: string,
  init: RequestInit = {},
  transform?: (data: unknown) => T,
): Promise<VersionedResource<T>> {
  const response = await apiFetch(path, init)
  return {
    data: transform ? await handleResponse(response, transform) : await handleResponse<T>(response),
    etag: response.headers.get('ETag'),
  }
}

// ============================================================================
// Dataset API
// ============================================================================

/**
 * Fetch all available datasets.
 */
export async function fetchDatasets(): Promise<DatasetInfo[]> {
  return apiRequest('/datasets', {}, (data) =>
    (data as Array<Record<string, unknown>>).map(preserveDatasetFeatureKeys),
  )
}

/**
 * Fetch a specific dataset by ID.
 */
export async function fetchDataset(datasetId: string): Promise<DatasetInfo> {
  return apiRequest(`/datasets/${datasetId}`, {}, (data) =>
    preserveDatasetFeatureKeys(data as Record<string, unknown>),
  )
}

/**
 * Fetch capabilities for a dataset.
 */
export async function fetchCapabilities(datasetId: string): Promise<DatasetCapabilities> {
  return apiRequest<DatasetCapabilities>(`/datasets/${datasetId}/capabilities`)
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
  const path = `/datasets/${datasetId}/episodes${query ? `?${query}` : ''}`
  return apiRequest<EpisodeMeta[]>(path)
}

/**
 * Fetch a specific episode by index.
 */
export async function fetchEpisode(datasetId: string, episodeIndex: number): Promise<EpisodeData> {
  return apiRequest(`/datasets/${datasetId}/episodes/${episodeIndex}`, {}, (data) =>
    preserveEpisodeVariableKeys(data as Record<string, unknown>),
  )
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
): Promise<VersionedResource<EpisodeAnnotationFile>> {
  return apiRequestVersioned<EpisodeAnnotationFile>(
    `/datasets/${datasetId}/episodes/${episodeIndex}/annotations`,
  )
}

/**
 * Save an annotation for an episode.
 */
export async function saveAnnotation(
  datasetId: string,
  episodeIndex: number,
  annotation: EpisodeAnnotation,
  precondition: MutationPrecondition,
): Promise<VersionedResource<EpisodeAnnotationFile>> {
  return apiRequestVersioned<EpisodeAnnotationFile>(
    `/datasets/${datasetId}/episodes/${episodeIndex}/annotations`,
    {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        ...mutationPreconditionHeaders(precondition),
      },
      body: JSON.stringify(annotation),
    },
  )
}

/**
 * Delete annotations for an episode.
 */
export async function deleteAnnotations(
  datasetId: string,
  episodeIndex: number,
  etag: string,
): Promise<{ deleted: boolean; episodeIndex: number }> {
  return apiRequest<{ deleted: boolean; episodeIndex: number }>(
    `/datasets/${datasetId}/episodes/${episodeIndex}/annotations`,
    { method: 'DELETE', headers: { 'If-Match': etag } },
  )
}

/**
 * Trigger auto-analysis for an episode.
 */
export async function triggerAutoAnalysis(
  datasetId: string,
  episodeIndex: number,
): Promise<AutoQualityAnalysis> {
  return apiRequest<AutoQualityAnalysis>(
    `/datasets/${datasetId}/episodes/${episodeIndex}/annotations/auto`,
    { method: 'POST' },
  )
}

/**
 * Fetch annotation summary for a dataset.
 */
export async function fetchAnnotationSummary(datasetId: string): Promise<AnnotationSummary> {
  return apiRequest<AnnotationSummary>(`/datasets/${datasetId}/annotations/summary`)
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
  return apiRequest<CacheStats>('/datasets/cache/stats')
}

/**
 * Warm the episode cache for a dataset by preloading the first N episodes.
 */
export async function warmCache(datasetId: string, count = 5): Promise<void> {
  await apiRequest(`/datasets/${datasetId}/cache/warm?count=${count}`, {
    method: 'POST',
  })
}

// ============================================================================
// VLM-as-Judge API
// ============================================================================

/** Status returned to the panel when the judge router is not mounted. */
const VLM_JUDGE_DISABLED: VlmJudgeStatus = {
  enabled: false,
  cached: false,
  judgeModel: null,
  promptVersion: null,
  cacheKey: null,
  result: null,
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
): Promise<VlmJudgeStatus> {
  const response = await apiFetch(`/datasets/${datasetId}/episodes/${episodeIndex}/judge`)
  if (response.status === 404) return VLM_JUDGE_DISABLED
  return handleResponse<VlmJudgeStatus>(response)
}

/**
 * Run the VLM judge on an episode (cache-first unless ``force`` is true).
 */
export async function runVlmJudge(
  datasetId: string,
  episodeIndex: number,
  options: VlmJudgeRunOptions = {},
): Promise<VlmJudgeResult> {
  return apiRequest<VlmJudgeResult>(`/datasets/${datasetId}/episodes/${episodeIndex}/judge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      instruction: options.instruction,
      views: options.views,
      process_method: options.processMethod,
      force: options.force ?? false,
    }),
  })
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
  precondition: MutationPrecondition,
): Promise<VersionedResource<EpisodeLabelsResult>> {
  return apiRequestVersioned<EpisodeLabelsResult>(
    `/datasets/${datasetId}/episodes/${episodeIndex}/labels`,
    {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        ...mutationPreconditionHeaders(precondition),
      },
      body: JSON.stringify({ labels }),
    },
  )
}
