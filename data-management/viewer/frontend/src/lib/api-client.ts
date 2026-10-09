/**
 * API client for robotic training data annotation backend.
 *
 * Provides type-safe API calls with error handling.
 */

import type {
  AnnotationSummary,
  AutoQualityAnalysis,
  Contribution,
  ContributionLedger,
  DatasetCapabilities,
  DatasetInfo,
  EpisodeAnnotation,
  EpisodeAnnotationFile,
  EpisodeData,
  EpisodeMeta,
  SavedInputSnapshot,
  VlmJudgeResult,
  VlmJudgeRunOptions,
  VlmJudgeStatus,
} from '@/types'
import type { JudgeJob } from '@/types/vlm-judge'

import { getAuthHeaders } from './auth-headers'
import { recordDiagnosticEvent } from './playback-diagnostics'

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

function camelToSnake(str: string): string {
  return str.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`)
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

export function preserveProvenance<T extends { provenance?: Record<string, ContributionLedger> }>(
  data: unknown,
): T {
  const result = transformKeys<T>(data)
  const raw = data as { provenance?: Record<string, Record<string, unknown>> }
  if (raw.provenance) {
    result.provenance = Object.fromEntries(
      Object.entries(raw.provenance).map(([scope, ledger]) => [
        scope,
        {
          ...transformKeys<ContributionLedger>(ledger),
          acceptances: structuredClone(ledger.acceptances ?? {}) as Record<string, string[]>,
          contributions: ((ledger.contributions ?? []) as Record<string, unknown>[]).map(
            (contribution) => ({
              ...transformKeys<Contribution>(contribution),
              value: structuredClone(contribution.value),
            }),
          ),
        },
      ]),
    )
  }
  return result
}

function transformKeysToSnake<T>(obj: unknown): T {
  if (Array.isArray(obj)) {
    return obj.map(transformKeysToSnake) as T
  }
  if (obj !== null && typeof obj === 'object') {
    return Object.fromEntries(
      Object.entries(obj as Record<string, unknown>).map(([key, value]) => [
        camelToSnake(key),
        transformKeysToSnake(value),
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
export interface DatasetSummary {
  id: string
  name: string
  group: string | null
  totalEpisodes: number
  format: string | null
}

export interface DatasetCatalogPage {
  items: DatasetSummary[]
  total: number
  catalogTotal: number
  groups: string[]
  snapshotId: string
  offset: number
  limit: number
  stale: boolean
  refreshFailed: boolean
}

export interface DatasetCatalogOptions {
  query?: string
  group?: string
  sort?: 'name' | 'episodes' | 'episodes-desc'
  offset?: number
  limit?: number
  snapshotId?: string
  refresh?: boolean
}

export async function fetchDatasetCatalog(
  options: DatasetCatalogOptions,
): Promise<DatasetCatalogPage> {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(options)) {
    if (value !== undefined) params.set(key === 'snapshotId' ? 'snapshot_id' : key, String(value))
  }
  return apiRequest(`/datasets/catalog?${params}`)
}

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
export async function fetchEpisode(
  datasetId: string,
  episodeIndex: number,
  signal?: AbortSignal,
): Promise<EpisodeData> {
  const controller = new AbortController()
  const requestSignal = signal ? AbortSignal.any([signal, controller.signal]) : controller.signal
  const started = performance.now()
  const timeout = setTimeout(
    () => controller.abort(new DOMException('Episode read timed out', 'TimeoutError')),
    60_000,
  )
  let onAbort: (() => void) | undefined
  let outcome = 'success'
  let status: number | undefined
  try {
    requestSignal.throwIfAborted()
    const aborted = new Promise<never>((_resolve, reject) => {
      onAbort = () => reject(requestSignal.reason)
      requestSignal.addEventListener('abort', onAbort, { once: true })
    })
    return await Promise.race([
      apiRequest(
        `/datasets/${datasetId}/episodes/${episodeIndex}`,
        { cache: 'no-store', signal: requestSignal },
        (data) => preserveEpisodeVariableKeys(data as Record<string, unknown>),
      ),
      aborted,
    ])
  } catch (error) {
    if (requestSignal.aborted) {
      const timedOut =
        controller.signal.aborted && requestSignal.reason === controller.signal.reason
      outcome = timedOut ? 'timeout' : 'cancelled'
      if (timedOut) {
        throw new ApiClientError('Episode loading timed out. Try again.', 'EPISODE_TIMEOUT', 0)
      }
      throw requestSignal.reason
    }
    status = error instanceof ApiClientError ? error.status : undefined
    outcome = status ? 'http-error' : 'request-error'
    throw error
  } finally {
    clearTimeout(timeout)
    if (onAbort) requestSignal.removeEventListener('abort', onAbort)
    recordDiagnosticEvent('workspace', 'episode-request-completed', {
      datasetId,
      episodeIndex,
      outcome,
      status,
      durationMs: Math.round(performance.now() - started),
    })
  }
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
    {},
    preserveProvenance<EpisodeAnnotationFile>,
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
      body: JSON.stringify({
        ...transformKeysToSnake<Record<string, unknown>>(annotation),
        ...(annotation.instructionAdoption
          ? { instruction_adoption: annotation.instructionAdoption }
          : {}),
      }),
    },
    preserveProvenance<EpisodeAnnotationFile>,
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
  const accepted = await apiRequest<JudgeJob>(
    `/datasets/${datasetId}/episodes/${episodeIndex}/judge`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({
        snapshot_id: options.snapshotId,
        annotation_author_id: options.annotationAuthorId,
        views: options.views,
        process_method: options.processMethod,
        force: options.force ?? false,
      }),
    },
  )
  if (!accepted.id || accepted.datasetId !== datasetId) throw new Error('Judge job scope changed.')
  for (let attempt = 0; attempt < 1800; attempt += 1) {
    const job = await fetchJudgeJob(accepted.id)
    if (job.datasetId !== datasetId || job.id !== accepted.id)
      throw new Error('Judge job scope changed.')
    if (job.status === 'succeeded') {
      const target = job.targets?.find((item) => item.episodeIndex === episodeIndex)
      if (
        !target?.result ||
        target.result.episodeId !== `${datasetId}/episode_${String(episodeIndex).padStart(6, '0')}`
      ) {
        throw new Error('Judge result does not match the submitted episode.')
      }
      return {
        ...target.result,
        cached: target.cached === true,
        processMethod: job.config.processMethod,
      }
    }
    if (['partial', 'failed', 'cancelled'].includes(job.status)) {
      throw new Error(`Judge job ${job.status}.`)
    }
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
  throw new Error('Judge job is still running.')
}

export async function submitJudgeJob(
  datasetId: string,
  submission: import('@/types/vlm-judge').JudgeSubmission,
  requestId: string,
): Promise<JudgeJob> {
  const options = submission.options ?? {}
  return apiRequest('/judge/jobs', {
    method: 'POST',
    headers: { 'Idempotency-Key': requestId },
    body: JSON.stringify({
      dataset_id: datasetId,
      episode_indices: submission.indices,
      mode: submission.mode,
      approval_id: submission.approvalId,
      snapshot_ids: submission.snapshotIds,
      samples:
        submission.samples &&
        Object.fromEntries(
          Object.entries(submission.samples).map(([index, reference]) => [
            index,
            {
              annotation_author_id: reference.annotationAuthorId,
              annotation_revision: reference.annotationRevision,
              snapshot_id: reference.snapshotId,
            },
          ]),
        ),
      options: {
        process_method: options.processMethod,
        views: options.views,
        annotation_author_id: options.annotationAuthorId,
        force: options.force,
      },
    }),
  })
}

export async function fetchJudgeJob(jobId: string): Promise<JudgeJob> {
  return apiRequest<JudgeJob>(`/judge/jobs/${encodeURIComponent(jobId)}`, { cache: 'no-store' })
}

export function fetchJudgeEvidence(datasetId: string, episodeIndex: number) {
  const params = new URLSearchParams({
    dataset_id: datasetId,
    episode_index: String(episodeIndex),
    limit: '25',
  })
  return apiRequest<{ items: import('@/types/vlm-judge').JudgeEvidence[]; total: number }>(
    `/judge/results?${params}`,
    { cache: 'no-store' },
  )
}

export function fetchJudgeInventory(datasetId: string, offset: number, snapshotId?: string) {
  const params = new URLSearchParams({ dataset_id: datasetId, offset: String(offset), limit: '25' })
  if (snapshotId) params.set('snapshot_id', snapshotId)
  return apiRequest<{ items: number[]; total: number; snapshotId: string }>(
    `/judge/episodes?${params}`,
    { cache: 'no-store' },
  )
}

export function fetchJudgeJobs(datasetId: string, offset = 0) {
  const params = new URLSearchParams({ dataset_id: datasetId, offset: String(offset), limit: '25' })
  return apiRequest<{ items: JudgeJob[]; total: number }>(`/judge/jobs?${params}`, {
    cache: 'no-store',
  })
}

export function fetchJudgeApprovals(datasetId: string) {
  const params = new URLSearchParams({ dataset_id: datasetId, limit: '100' })
  return apiRequest<{ items: import('@/types/vlm-judge').JudgeApproval[]; total: number }>(
    `/judge/approvals?${params}`,
    { cache: 'no-store' },
  )
}

export async function fetchJudgeReset(datasetId: string) {
  try {
    return await apiRequest<import('@/types/vlm-judge').JudgeReset>(
      `/judge/resets?${new URLSearchParams({ dataset_id: datasetId })}`,
      { cache: 'no-store' },
    )
  } catch (error) {
    if (error instanceof ApiClientError && error.status === 404) return null
    throw error
  }
}

export function mutateJudgeDataset(
  datasetId: string,
  action: import('@/types/vlm-judge').JudgeDatasetAction,
) {
  const path =
    'jobId' in action
      ? `/jobs/${encodeURIComponent(action.jobId)}/${action.kind}`
      : action.kind === 'preview-reset'
        ? '/resets/preview'
        : action.kind === 'retry-reset'
          ? '/resets/retry'
          : '/resets'
  const body =
    action.kind === 'approve'
      ? { acknowledge_exceptions: action.acknowledgeExceptions }
      : action.kind === 'apply'
        ? { episode_indices: action.indices }
        : action.kind === 'confirm-reset'
          ? { dataset_id: datasetId, preview_id: action.previewId }
          : 'jobId' in action
            ? {}
            : { dataset_id: datasetId }
  return apiRequest<
    | JudgeJob
    | import('@/types/vlm-judge').JudgeApproval
    | import('@/types/vlm-judge').JudgeResetPreview
    | import('@/types/vlm-judge').JudgeReset
  >(`/judge${path}`, { method: 'POST', body: JSON.stringify(body) })
}

export async function fetchVlmJudgeSnapshot(
  datasetId: string,
  episodeIndex: number,
  authorId?: string,
): Promise<SavedInputSnapshot> {
  const query = authorId ? `?annotation_author_id=${encodeURIComponent(authorId)}` : ''
  return apiRequest<SavedInputSnapshot>(
    `/datasets/${datasetId}/episodes/${episodeIndex}/judge/snapshot${query}`,
    { cache: 'no-store' },
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
  precondition: MutationPrecondition,
  intent: 'human-edit' | 'legacy-unknown' = 'legacy-unknown',
): Promise<VersionedResource<EpisodeLabelsResult>> {
  return apiRequestVersioned<EpisodeLabelsResult>(
    `/datasets/${datasetId}/episodes/${episodeIndex}/labels`,
    {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        ...mutationPreconditionHeaders(precondition),
      },
      body: JSON.stringify({ labels, intent }),
    },
  )
}
