/**
 * Typed client for the curated dataset release API (`/api/releases/v1`).
 *
 * Requests use the backend's snake_case contract. Responses are camel-cased. Release failures
 * carry the backend `detail.code` (for example `stale_preview`) on {@link ReleaseRequestError}.
 */

import { apiFetch, transformKeys } from '@/lib/api-client'

/** Client-side release selection for one source dataset. */
export interface ReleaseRequest {
  /** Destination alias from the capabilities response; filesystem paths are not accepted. */
  destination: string
  packageId: string
  attemptId: string
  /** Source dataset identifier. */
  datasetId: string
  datasetName?: string
  description?: string
  /** Source episode indices to release. */
  episodeIndices: number[]
  /** Saved selections forwarded as-is to the backend. */
  resources?: Record<string, unknown>[]
  finalized?: boolean
}

/** Digests and acknowledgments bound to one accepted preview. */
export interface ReleaseDigests {
  previewDigest: string
  processingDigest: string
  localDiskAcknowledged: boolean
}

/** Digests plus the caller idempotency key required to submit a release job. */
export interface ReleaseSubmission extends ReleaseDigests {
  idempotencyKey: string
}

/** Advisory capabilities reported by the release API. */
export interface ReleaseCapabilities {
  schema: string
  jobStore: boolean
  planning: boolean
  destinations: { alias: string }[]
  operations: string[]
  target: { name: string; version: string }
  routes: string[]
  capacity: string
}

/**
 * Per-episode evidence reported by the source inventory. The population page carries only
 * `episodeIndex` and `labels` (`detailed` is false). Annotation, edits, resources, provenance and judge
 * fields are null or empty until the episode's details are loaded (`detailed` is true).
 */
export interface ReleaseInventoryItem {
  episodeIndex: number
  /** True when the annotation, edit, resource, provenance and judge fields were loaded for this episode. */
  detailed: boolean
  resources: Record<string, unknown>[] | null
  annotation: boolean | null
  edits: boolean | null
  labels: string[] | null
  provenance: { origins: string[] } | null
  judge: {
    status: string
    freshness: string
    confidence?: number | null
    outcome?: string | null
  } | null
}

/** Details for a set of episodes from the source inventory details endpoint. */
export interface ReleaseInventoryDetailsPage {
  schema: string
  items: ReleaseInventoryItem[]
}

/** One page of the full source population. */
export interface ReleaseInventoryPage {
  schema: string
  items: ReleaseInventoryItem[]
  total: number
  nextCursor: number | null
}

/** Lifecycle state of a release job as reported by the status endpoint. */
export type ReleaseJobState =
  | 'accepted'
  | 'running'
  | 'needs_input'
  | 'cancelling'
  | 'sealed'
  | 'committing'
  | 'committed'
  | 'cancelled'

/** Client-safe status of one release job. Paths and frozen requests are never returned. */
export interface ReleaseJobStatus {
  jobId: string
  state: ReleaseJobState
  cancelRequested: boolean
  effectsStopped: boolean
  cleanupState: string
  capacityState: string
  destinationCurrentBytes: number
  destinationPeakBytes: number
  schema: string
  scratchBytes: number
  peakScratchBytes: number
  ioCounters: Record<string, number>
  diagnostics: Record<string, unknown>
  /** The latest execution attempt failed and is awaiting a worker retry. */
  recoveryRequired: boolean
  refusalCode?: string | null
}

/** Job status as listed for one source. The list endpoint adds the source dataset identifier. */
export interface ReleaseJobListItem extends ReleaseJobStatus {
  datasetId: string | null
  /** Durable acceptance time, or null when an older job has no source association. */
  createdAt?: string | null
}

/** One page of release jobs owned by the caller, ordered by acceptance. */
export interface ReleaseJobPage {
  items: ReleaseJobListItem[]
  nextCursor: number | null
}

/** Result of a committed release job; job identity comes from its status, not this response. */
export interface ReleaseResult {
  schema: string
  result: Record<string, unknown>
  artifacts: string[]
}

/** One committed release artifact. Content is a parsed JSON value, or text for the report. */
export interface ReleaseArtifact {
  schema: string
  artifact: string
  content: unknown
}

/** Verified local release summary, identified by an opaque handle. */
export interface SourceReleaseSummary {
  handle: string
  datasetName: string | null
  packageId: string
  manifestSha256: string
  /** Creation time recorded when the release request was accepted. */
  createdAt?: string | null
}

/** Verified local release with its saved annotations, labels and original frozen evidence. */
export interface SourceRelease {
  handle: string
  schema: string
  totalEpisodes: number
  annotations: Record<string, unknown>[]
  labels: { episodes: Record<string, string[]>; [key: string]: unknown } | null
  /** Original frozen input evidence, separate from output-coordinate projections. */
  frozenEvidence: Record<string, unknown> | null
}

/** Releases visible to the caller for one source. */
export interface SourceReleasePage {
  items: SourceReleaseSummary[]
}

/** Native episode metadata from a verified local release. Paths are never returned. */
export interface SourceEpisode {
  episodeIndex: number
  episodeId: string
  instruction: string
  fps: number
  length: number
  fromTimestamp: number
  toTimestamp: number
  cameras: string[]
  videoWindows?: Record<string, [number, number]>
}

/** One page of native episode metadata. */
export interface SourceEpisodePage {
  items: SourceEpisode[]
  total: number
  nextCursor: number | null
}

/** Verified description of a release body. No destination output is reserved or published. */
export interface ReleasePreview {
  schema: string
  digest: string
  reservedBytes: number
  summary: { processingDigest?: string; [key: string]: unknown }
}

/** Accepted release job. Replays with the same idempotency key return the existing job. */
export interface AcceptedRelease {
  jobId: string
  replayed: boolean
}

/** Release API failure carrying the backend error code. */
export class ReleaseRequestError extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly status: number,
  ) {
    super(message)
    this.name = 'ReleaseRequestError'
  }

  /** Whether submission was refused rather than possibly accepted with a lost response. */
  get isDefinitiveRejection(): boolean {
    return (
      (this.status >= 400 && this.status < 500) ||
      (this.status === 507 && this.code === 'insufficient capacity for the reserved estimate')
    )
  }
}

const NO_DIGESTS: ReleaseDigests = {
  previewDigest: '',
  processingDigest: '',
  localDiskAcknowledged: false,
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

/**
 * Serialize a release selection into the backend request body.
 *
 * @param request - Selection to serialize.
 * @param digests - Confirmed digests; empty values for preview.
 * @returns JSON body using backend field names.
 */
function toReleaseBody(request: ReleaseRequest, digests: ReleaseDigests): string {
  return JSON.stringify({
    destination: request.destination,
    package_id: request.packageId,
    attempt_id: request.attemptId,
    dataset_id: request.datasetId,
    dataset_name: request.datasetName ?? null,
    description: request.description ?? '',
    episode_indices: request.episodeIndices,
    resources: request.resources ?? [],
    finalized: request.finalized ?? false,
    preview_digest: digests.previewDigest,
    processing_digest: digests.processingDigest,
    local_disk_acknowledged: digests.localDiskAcknowledged,
  })
}

/**
 * Build a {@link ReleaseRequestError} from a failed response.
 *
 * @param response - Non-OK response from the release API.
 * @returns Error with the backend `detail.code`, or `HTTP_<status>` when the body is not JSON.
 */
async function releaseFailure(response: Response): Promise<ReleaseRequestError> {
  let code = `HTTP_${response.status}`
  try {
    const payload: unknown = await response.json()
    const detail = isRecord(payload) ? payload.detail : undefined
    if (isRecord(detail) && typeof detail.code === 'string') {
      code = detail.code
    }
  } catch {
    // Keep the status-derived code for non-JSON failures.
  }
  return new ReleaseRequestError(`Release request failed: ${code}`, code, response.status)
}

/**
 * Perform one release API request and camel-case the JSON response.
 *
 * @param path - Path relative to `/api`.
 * @param init - Optional request init; omitted for GET requests.
 * @returns Parsed, camel-cased response body.
 * @throws {@link ReleaseRequestError} for non-OK responses.
 */
async function releaseRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = init ? await apiFetch(path, init) : await apiFetch(path)
  if (!response.ok) {
    throw await releaseFailure(response)
  }
  return transformKeys<T>(await response.json())
}

/**
 * Read the release API capabilities for this caller.
 *
 * @returns Advisory capabilities; no filesystem paths.
 */
export function getReleaseCapabilities(): Promise<ReleaseCapabilities> {
  return releaseRequest<ReleaseCapabilities>('/releases/v1/capabilities')
}

/**
 * Read one page of the full source population for release selection.
 *
 * @param datasetId - Source dataset identifier.
 * @param page - Cursor and page size (1 to 100).
 * @param signal - Cancels the request when its source or principal is no longer active.
 * @returns Page of episode indices with the total population size.
 */
export function getReleaseSourceInventory(
  datasetId: string,
  page: { cursor: number; limit: number },
  signal?: AbortSignal,
): Promise<ReleaseInventoryPage> {
  const query = new URLSearchParams({
    cursor: String(page.cursor),
    limit: String(page.limit),
  })
  return releaseRequest<ReleaseInventoryPage>(
    `/releases/v1/inventory/${encodeURIComponent(datasetId)}?${query.toString()}`,
    signal ? { signal } : undefined,
  )
}

/**
 * Read full evidence for specific episodes of the source population. Requests are capped by the backend.
 *
 * @param datasetId - Source dataset identifier.
 * @param episodes - Distinct episode indices from the population.
 * @param signal - Cancels the request when its source or principal is no longer active.
 * @returns Detailed items for the requested episodes.
 */
export function getReleaseSourceInventoryDetails(
  datasetId: string,
  episodes: number[],
  signal?: AbortSignal,
): Promise<ReleaseInventoryDetailsPage> {
  const query = new URLSearchParams({ episodes: episodes.join(',') })
  return releaseRequest<ReleaseInventoryDetailsPage>(
    `/releases/v1/inventory/${encodeURIComponent(datasetId)}/details?${query.toString()}`,
    signal ? { signal } : undefined,
  )
}

/**
 * Describe a release selection without accepting a job or reserving output.
 *
 * @param request - Selection to describe.
 * @param signal - Aborts the transport only. Aborting sends no cancellation; no job is accepted or output reserved.
 * @returns Digest, reserved bytes, and summary to confirm before submission.
 */
export function previewRelease(
  request: ReleaseRequest,
  signal?: AbortSignal,
): Promise<ReleasePreview> {
  return releaseRequest<ReleasePreview>('/releases/v1/preview', {
    method: 'POST',
    body: toReleaseBody(request, NO_DIGESTS),
    signal,
  })
}

/**
 * Accept a release job bound to a confirmed preview. Aborting only stops observation: the request may
 * still be accepted, so the caller must retain the idempotency key and resolve it by lookup.
 *
 * @param request - Selection that was previewed.
 * @param submission - Confirmed digests and the caller idempotency key.
 * @param signal - Aborts the transport only. Sends no cancellation.
 * @returns Accepted job identifier and whether the request replayed an existing job.
 * @throws {Error} When the idempotency key is empty.
 * @throws {@link ReleaseRequestError} for rejected submissions, such as `stale_preview`.
 */
export async function submitRelease(
  request: ReleaseRequest,
  submission: ReleaseSubmission,
  signal?: AbortSignal,
): Promise<AcceptedRelease> {
  if (!submission.idempotencyKey) {
    throw new Error('An idempotency key is required to submit a release')
  }
  return releaseRequest<AcceptedRelease>('/releases/v1/jobs', {
    method: 'POST',
    headers: { 'Idempotency-Key': submission.idempotencyKey },
    body: toReleaseBody(request, submission),
    signal,
  })
}

/**
 * Read the status of one caller-owned release job.
 *
 * @param jobId - Job identifier returned on acceptance.
 * @returns Client-safe job status.
 */
export function getReleaseJob(jobId: string): Promise<ReleaseJobStatus> {
  return releaseRequest<ReleaseJobStatus>(`/releases/v1/jobs/${encodeURIComponent(jobId)}`)
}

/**
 * Resolve a previously accepted release by its idempotency key without submitting anything.
 *
 * @param idempotencyKey - Key sent with the original submission.
 * @returns Accepted job identifier with `replayed: true`.
 * @throws {@link ReleaseRequestError} with code `request_not_found` (HTTP 404) when no accepted job exists for the key.
 */
export function lookupAcceptedRelease(idempotencyKey: string): Promise<AcceptedRelease> {
  return releaseRequest<AcceptedRelease>('/releases/v1/requests', {
    method: 'GET',
    headers: { 'Idempotency-Key': idempotencyKey },
  })
}

/**
 * List release jobs owned by the caller, ordered by acceptance.
 *
 * @param page - Cursor, page size (1 to 100), and optional source dataset filter.
 * @returns Page of job statuses for the requested source.
 */
export function listReleaseJobs(page: {
  cursor: number
  limit: number
  datasetId?: string
}): Promise<ReleaseJobPage> {
  const query = new URLSearchParams({ cursor: String(page.cursor), limit: String(page.limit) })
  if (page.datasetId) {
    query.set('dataset_id', page.datasetId)
  }
  return releaseRequest<ReleaseJobPage>(`/releases/v1/jobs?${query.toString()}`)
}

/**
 * Request cancellation of a caller-owned release job. Does not clear any saved job pointer.
 *
 * @param jobId - Job identifier.
 * @returns Accepted status snapshot.
 */
export function cancelReleaseJob(jobId: string): Promise<ReleaseJobStatus> {
  return releaseRequest<ReleaseJobStatus>(`/releases/v1/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST',
  })
}

/**
 * Request recovery of a caller-owned release job that is stuck in a recoverable state.
 *
 * @param jobId - Job identifier.
 * @returns Accepted status snapshot.
 */
export function recoverReleaseJob(jobId: string): Promise<ReleaseJobStatus> {
  return releaseRequest<ReleaseJobStatus>(
    `/releases/v1/jobs/${encodeURIComponent(jobId)}/recover`,
    {
      method: 'POST',
    },
  )
}

/**
 * Read the verified result of a committed release job.
 *
 * @param jobId - Job identifier.
 * @returns Verification result and artifact names. Fails with `release_result_not_verified` before commit.
 */
export function getReleaseResult(jobId: string): Promise<ReleaseResult> {
  return releaseRequest<ReleaseResult>(`/releases/v1/jobs/${encodeURIComponent(jobId)}/result`)
}

/**
 * Read one committed release artifact. Content keys are returned as stored, not camel-cased.
 *
 * @param jobId - Job identifier.
 * @param artifact - One of `manifest`, `inventory`, `receipt`, or `report`.
 * @returns Artifact content.
 */
export async function getReleaseArtifact(
  jobId: string,
  artifact: string,
): Promise<ReleaseArtifact> {
  const response = await apiFetch(
    `/releases/v1/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(artifact)}`,
  )
  if (!response.ok) {
    throw await releaseFailure(response)
  }
  const payload: unknown = await response.json()
  if (!isRecord(payload)) {
    throw new ReleaseRequestError(
      'Release artifact response was not an object',
      'invalid_artifact',
      response.status,
    )
  }
  return payload as unknown as ReleaseArtifact
}

/**
 * List verified local releases of a source visible to the caller.
 *
 * @param datasetId - Source dataset identifier.
 * @returns Release summaries; no paths or aliases.
 */
export function listSourceReleases(datasetId: string): Promise<SourceReleasePage> {
  return releaseRequest<SourceReleasePage>(
    `/releases/v1/sources/${encodeURIComponent(datasetId)}/releases`,
  )
}

/**
 * Read one verified source release by opaque handle.
 *
 * @param datasetId - Source dataset identifier.
 * @param handle - 64-character lowercase hex handle from {@link listSourceReleases}.
 * @returns Camel-cased envelope with annotation, label and frozen evidence keys preserved as stored.
 */
export async function getSourceRelease(datasetId: string, handle: string): Promise<SourceRelease> {
  const response = await apiFetch(
    `/releases/v1/sources/${encodeURIComponent(datasetId)}/releases/${encodeURIComponent(handle)}`,
  )
  if (!response.ok) {
    throw await releaseFailure(response)
  }
  const payload = (await response.json()) as Record<string, unknown>
  const { annotations, labels, frozen_evidence: frozenEvidence, ...envelope } = payload
  return {
    ...transformKeys<Omit<SourceRelease, 'annotations' | 'labels' | 'frozenEvidence'>>(envelope),
    annotations: annotations as SourceRelease['annotations'],
    labels: labels as SourceRelease['labels'],
    frozenEvidence: frozenEvidence as SourceRelease['frozenEvidence'],
  }
}

/**
 * Page native episode metadata of a verified local release.
 *
 * @param datasetId - Source dataset identifier.
 * @param handle - Release handle.
 * @param page - Cursor and page size (1 to 100).
 * @returns Page of episode metadata.
 */
export function listSourceEpisodes(
  datasetId: string,
  handle: string,
  page: { cursor: number; limit: number },
): Promise<SourceEpisodePage> {
  const query = new URLSearchParams({ cursor: String(page.cursor), limit: String(page.limit) })
  return releaseRequest<SourceEpisodePage>(
    `/releases/v1/sources/${encodeURIComponent(datasetId)}/releases/${encodeURIComponent(handle)}/episodes?${query.toString()}`,
  )
}

/**
 * Fetch one episode camera stream as an MP4 blob. The endpoint requires auth, so a plain media URL cannot be used.
 *
 * @param datasetId - Source dataset identifier.
 * @param handle - Release handle.
 * @param episodeIndex - Source episode index.
 * @param camera - Camera name from {@link SourceEpisode.cameras}.
 * @returns MP4 blob. Create an object URL from it and revoke the URL when done.
 * @throws {@link ReleaseRequestError} for non-OK responses.
 */
export async function fetchSourceEpisodeMedia(
  datasetId: string,
  handle: string,
  episodeIndex: number,
  camera: string,
  signal?: AbortSignal,
): Promise<Blob> {
  const path =
    `/releases/v1/sources/${encodeURIComponent(datasetId)}/releases/${encodeURIComponent(handle)}` +
    `/episodes/${episodeIndex}/media/${encodeURIComponent(camera)}`
  const response = await apiFetch(path, { signal })
  if (!response.ok) {
    throw await releaseFailure(response)
  }
  return response.blob()
}
