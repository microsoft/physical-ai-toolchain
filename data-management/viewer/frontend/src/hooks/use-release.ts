import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useState } from 'react'

import {
  type AcceptedRelease,
  cancelReleaseJob,
  getReleaseCapabilities,
  getReleaseJob,
  getReleaseResult,
  getReleaseSourceInventory,
  listReleaseJobs,
  lookupAcceptedRelease,
  previewRelease,
  recoverReleaseJob,
  type ReleaseInventoryItem,
  type ReleaseJobState,
  type ReleaseRequest,
  ReleaseRequestError,
  type ReleaseSubmission,
  submitRelease,
} from '@/api/releases'

const INVENTORY_PAGE_SIZE = 100
const JOB_PAGE_SIZE = 20
const ACTIVE_JOB_PREFIX = 'release-active-job:v2'
const PENDING_SUBMISSION_PREFIX = 'release-pending-submission:v1'
const IDEMPOTENCY_KEY_PATTERN = /^[A-Za-z0-9._:-]{8,128}$/
const MAX_INVENTORY_ITEMS = 10000
const POLL_INTERVAL_MS = 2000
const MAX_STATUS_POLLS = 900
const JOB_ID_PATTERN = /^[0-9a-f]{32}$/
const TERMINAL_STATES: ReadonlySet<ReleaseJobState> = new Set<ReleaseJobState>([
  'committed',
  'cancelled',
])

/** Full source population gathered from every inventory page. */
export interface ReleaseInventory {
  items: ReleaseInventoryItem[]
  total: number
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

/**
 * Build the storage scope segment. A JSON tuple keeps identifiers that contain separators from colliding.
 *
 * @param datasetId - Source dataset identifier.
 * @param scopeId - Principal scope identifier.
 * @param sourceIdentity - Optional source identity.
 * @returns Stable JSON encoding of the scope.
 */
function scopeSegment(
  datasetId: string,
  scopeId: string,
  sourceIdentity: string | undefined,
): string {
  return JSON.stringify([datasetId, scopeId, sourceIdentity ?? null])
}

/**
 * Build the localStorage key for the active job pointer. The key is scoped so one caller's pointer never
 * applies to another caller, dataset, or source identity.
 *
 * @param datasetId - Source dataset identifier.
 * @param scopeId - Principal scope identifier.
 * @param sourceIdentity - Optional source identity that further separates pointers.
 * @returns Storage key for the pointer.
 */
function activeJobStorageKey(
  datasetId: string,
  scopeId: string,
  sourceIdentity: string | undefined,
): string {
  return `${ACTIVE_JOB_PREFIX}:${scopeSegment(datasetId, scopeId, sourceIdentity)}`
}

/**
 * Build the localStorage key for a submission whose response may have been lost. Its idempotency key lets a
 * reload look the accepted job up without submitting again.
 *
 * @param datasetId - Source dataset identifier.
 * @param scopeId - Principal scope identifier.
 * @param sourceIdentity - Optional source identity.
 * @returns Storage key for the pending submission.
 */
function pendingSubmissionStorageKey(
  datasetId: string,
  scopeId: string,
  sourceIdentity: string | undefined,
): string {
  return `${PENDING_SUBMISSION_PREFIX}:${scopeSegment(datasetId, scopeId, sourceIdentity)}`
}

/**
 * Read a saved pending idempotency key. Values that do not match the key format are ignored.
 *
 * @param key - Storage key from {@link pendingSubmissionStorageKey}.
 * @returns Idempotency key, or `null` when absent, invalid, or storage is unavailable.
 */
function readPendingKey(key: string): string | null {
  try {
    const raw = globalThis.localStorage?.getItem(key) ?? null
    if (raw === null) {
      return null
    }
    const payload: unknown = JSON.parse(raw)
    const value = isRecord(payload) ? payload.idempotencyKey : undefined
    return typeof value === 'string' && IDEMPOTENCY_KEY_PATTERN.test(value) ? value : null
  } catch {
    return null
  }
}

/**
 * Persist a pending idempotency key before the submission request is sent.
 *
 * @param key - Storage key from {@link pendingSubmissionStorageKey}.
 * @param idempotencyKey - Key sent with the submission.
 */
function writePendingKey(key: string, idempotencyKey: string): void {
  try {
    globalThis.localStorage?.setItem(key, JSON.stringify({ idempotencyKey }))
  } catch {
    // Storage is unavailable; recovery after reload is not possible for this submission.
  }
}

/**
 * Read a saved job pointer. Values that are not job identifiers are ignored.
 *
 * @param key - Storage key from {@link activeJobStorageKey}.
 * @returns Valid job identifier, or null when absent or storage is unavailable.
 */
function readPointer(key: string): string | null {
  try {
    const value = globalThis.localStorage?.getItem(key) ?? null
    return value !== null && JOB_ID_PATTERN.test(value) ? value : null
  } catch {
    return null
  }
}

/**
 * Persist a job pointer. Storage failures are ignored; the in-memory pointer still applies.
 *
 * @param key - Storage key from {@link activeJobStorageKey}.
 * @param jobId - Accepted job identifier.
 */
function writePointer(key: string, jobId: string): void {
  try {
    globalThis.localStorage?.setItem(key, jobId)
  } catch {
    // Storage is unavailable; the pointer lasts for this session only.
  }
}

/**
 * Remove a saved job pointer. Storage failures are ignored.
 *
 * @param key - Storage key from {@link activeJobStorageKey}.
 */
function clearPointer(key: string): void {
  try {
    globalThis.localStorage?.removeItem(key)
  } catch {
    // Nothing to clear when storage is unavailable.
  }
}

/**
 * Load every inventory page sequentially and validate the population before returning it.
 *
 * @param datasetId - Source dataset identifier.
 * @returns All items with the reported total.
 * @throws {Error} When the cursor does not advance, the total changes between pages, the population exceeds
 * the client limit, or the collected items do not match the total.
 */
async function fetchFullInventory(datasetId: string): Promise<ReleaseInventory> {
  const items: ReleaseInventoryItem[] = []
  const seenEpisodes = new Set<number>()
  let cursor = 0
  let total: number | null = null
  for (;;) {
    const page = await getReleaseSourceInventory(datasetId, { cursor, limit: INVENTORY_PAGE_SIZE })
    if (total !== null && page.total !== total) {
      throw new Error('inventory_total_changed')
    }
    total = page.total
    if (total > MAX_INVENTORY_ITEMS) {
      throw new Error('inventory_too_large')
    }
    if (page.items.length === 0 && page.nextCursor !== null) {
      throw new Error('inventory_empty_page')
    }
    for (const item of page.items) {
      if (seenEpisodes.has(item.episodeIndex)) {
        throw new Error('inventory_duplicate_episode')
      }
      seenEpisodes.add(item.episodeIndex)
      items.push(item)
    }
    if (items.length > total) {
      throw new Error('inventory_overflow')
    }
    if (page.nextCursor === null) {
      break
    }
    if (page.nextCursor <= cursor) {
      throw new Error('inventory_cursor_not_advancing')
    }
    cursor = page.nextCursor
  }
  if (items.length !== total) {
    throw new Error('inventory_incomplete')
  }
  return { items, total }
}

/**
 * Manage one release workflow for a source dataset: population inventory, previews, job submission,
 * job status and result, and the saved pointer to the caller's active job.
 *
 * Restoring a saved pointer never submits anything. Nothing is submitted without an explicit call to `submit`.
 * `cancel` and `recover` act on the active job and keep the pointer. `startNew` only forgets the pointer and
 * never cancels a job.
 *
 * @param datasetId - Source dataset identifier.
 * @param scopeId - Principal scope identifier used to isolate caches and pointers.
 * @param sourceIdentity - Optional identity that further separates saved pointers.
 * @returns Query state, the active job identifier, and workflow actions.
 */
export function useRelease(datasetId: string, scopeId: string, sourceIdentity?: string) {
  const queryClient = useQueryClient()
  const storageKey = activeJobStorageKey(datasetId, scopeId, sourceIdentity)
  const pendingStorageKey = pendingSubmissionStorageKey(datasetId, scopeId, sourceIdentity)
  const [pointer, setPointer] = useState<{ key: string; jobId: string | null }>({
    key: '',
    jobId: null,
  })
  const [pending, setPending] = useState<{ key: string; idempotencyKey: string | null }>({
    key: '',
    idempotencyKey: null,
  })
  const jobId = pointer.key === storageKey ? pointer.jobId : readPointer(storageKey)
  const pendingIdempotencyKey =
    pending.key === pendingStorageKey ? pending.idempotencyKey : readPendingKey(pendingStorageKey)

  const inventory = useQuery({
    queryKey: ['releases', 'inventory', scopeId, datasetId, sourceIdentity ?? null],
    queryFn: () => fetchFullInventory(datasetId),
    enabled: Boolean(datasetId && scopeId),
    staleTime: 60_000,
  })

  const capabilities = useQuery({
    queryKey: ['releases', 'capabilities', scopeId],
    queryFn: getReleaseCapabilities,
    enabled: Boolean(scopeId),
    staleTime: 60_000,
  })

  const status = useQuery({
    queryKey: ['releases', 'job', scopeId, jobId],
    queryFn: () => getReleaseJob(jobId as string),
    enabled: Boolean(jobId && scopeId),
    refetchInterval: (query) => {
      const state = query.state.data?.state
      if (state === undefined || TERMINAL_STATES.has(state)) {
        return false
      }
      return query.state.dataUpdateCount >= MAX_STATUS_POLLS ? false : POLL_INTERVAL_MS
    },
  })

  const result = useQuery({
    queryKey: ['releases', 'result', scopeId, jobId],
    queryFn: () => getReleaseResult(jobId as string),
    enabled: Boolean(jobId && scopeId && status.data?.state === 'committed'),
  })

  const jobs = useInfiniteQuery({
    queryKey: ['releases', 'jobs', scopeId, datasetId],
    queryFn: ({ pageParam }) =>
      listReleaseJobs({ cursor: pageParam, limit: JOB_PAGE_SIZE, datasetId }),
    initialPageParam: 0,
    getNextPageParam: (page) => page.nextCursor ?? undefined,
    enabled: Boolean(datasetId && scopeId),
  })

  const previewMutation = useMutation({
    mutationFn: (request: ReleaseRequest) => previewRelease(request),
  })

  const submitMutation = useMutation({
    mutationFn: ({
      request,
      submission,
    }: {
      request: ReleaseRequest
      submission: ReleaseSubmission
    }) => submitRelease(request, submission),
  })

  const pendingLookup = useQuery({
    queryKey: ['releases', 'lookup', scopeId, storageKey, pendingStorageKey, pendingIdempotencyKey],
    queryFn: async (): Promise<AcceptedRelease> => {
      const accepted = await lookupAcceptedRelease(pendingIdempotencyKey as string)
      writePointer(storageKey, accepted.jobId)
      clearPointer(pendingStorageKey)
      setPointer({ key: storageKey, jobId: accepted.jobId })
      setPending({ key: pendingStorageKey, idempotencyKey: null })
      void queryClient.invalidateQueries({ queryKey: ['releases', 'jobs', scopeId] })
      return accepted
    },
    enabled: Boolean(scopeId && pendingIdempotencyKey && !jobId) && !submitMutation.isPending,
    retry: false,
  })

  const cancelMutation = useMutation({
    mutationFn: (id: string) => cancelReleaseJob(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['releases', 'job', scopeId] }),
  })

  const recoverMutation = useMutation({
    mutationFn: (id: string) => recoverReleaseJob(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['releases', 'job', scopeId] }),
  })

  const { mutateAsync: submitAsync } = submitMutation
  const { mutateAsync: cancelAsync } = cancelMutation
  const { mutateAsync: recoverAsync } = recoverMutation

  /**
   * Submit a previewed release. The idempotency key is saved first, so a lost response can be resolved by
   * lookup after reload instead of submitting again. The accepted job becomes the active job for this scope.
   *
   * @param request - Selection that was previewed.
   * @param submission - Confirmed digests and the caller idempotency key.
   * @returns Accepted job identifier.
   */
  const submit = useCallback(
    async (request: ReleaseRequest, submission: ReleaseSubmission): Promise<AcceptedRelease> => {
      const activeKey = storageKey
      const pendingKey = pendingStorageKey
      writePendingKey(pendingKey, submission.idempotencyKey)
      setPending({ key: pendingKey, idempotencyKey: submission.idempotencyKey })
      try {
        const accepted = await submitAsync({ request, submission })
        writePointer(activeKey, accepted.jobId)
        clearPointer(pendingKey)
        setPointer({ key: activeKey, jobId: accepted.jobId })
        setPending({ key: pendingKey, idempotencyKey: null })
        void queryClient.invalidateQueries({ queryKey: ['releases', 'jobs', scopeId] })
        return accepted
      } catch (error) {
        if (error instanceof ReleaseRequestError && error.isDefinitiveRejection) {
          clearPointer(pendingKey)
          setPending({ key: pendingKey, idempotencyKey: null })
          if (error.code === 'stale_preview') {
            void queryClient.invalidateQueries({
              queryKey: ['releases', 'inventory', scopeId, datasetId, sourceIdentity ?? null],
            })
          }
        }
        throw error
      }
    },
    [storageKey, pendingStorageKey, scopeId, datasetId, sourceIdentity, submitAsync, queryClient],
  )

  /**
   * Request cancellation of the active job. The saved pointer is kept.
   *
   * @returns Promise that resolves when the request is accepted.
   * @throws {Error} When there is no active job.
   */
  const cancel = useCallback(() => {
    if (!jobId) {
      return Promise.reject(new Error('no_active_job'))
    }
    return cancelAsync(jobId)
  }, [jobId, cancelAsync])

  /**
   * Request recovery of the active job. The saved pointer is kept.
   *
   * @returns Promise that resolves when the request is accepted.
   * @throws {Error} When there is no active job.
   */
  const recover = useCallback(() => {
    if (!jobId) {
      return Promise.reject(new Error('no_active_job'))
    }
    return recoverAsync(jobId)
  }, [jobId, recoverAsync])

  /**
   * Forget the saved active job and any pending submission for this scope so a new release can start. Makes no
   * API call and does not cancel the previous job.
   */
  const startNew = useCallback(() => {
    clearPointer(storageKey)
    clearPointer(pendingStorageKey)
    setPointer({ key: storageKey, jobId: null })
    setPending({ key: pendingStorageKey, idempotencyKey: null })
  }, [storageKey, pendingStorageKey])

  /**
   * Make a job from the source job list the active job. Does not cancel or submit anything.
   *
   * @param selectedJobId - Job identifier from the list.
   * @throws {Error} When the identifier is malformed.
   */
  const selectJob = useCallback(
    (selectedJobId: string) => {
      if (!JOB_ID_PATTERN.test(selectedJobId)) {
        throw new Error('invalid_job_id')
      }
      writePointer(storageKey, selectedJobId)
      setPointer({ key: storageKey, jobId: selectedJobId })
    },
    [storageKey],
  )

  return {
    inventory,
    inventoryReady: inventory.isSuccess && !inventory.isFetching,
    capabilities,
    activeJobId: jobId,
    hasPendingSubmission: pendingIdempotencyKey !== null,
    pendingLookup,
    jobs,
    selectJob,
    status,
    result,
    preview: previewMutation.mutateAsync,
    isPreviewing: previewMutation.isPending,
    submit,
    isSubmitting: submitMutation.isPending,
    cancel,
    isCancelling: cancelMutation.isPending,
    recover,
    isRecovering: recoverMutation.isPending,
    startNew,
  }
}
