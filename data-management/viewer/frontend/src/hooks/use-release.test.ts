import { act, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as releaseApi from '@/api/releases'
import { renderHookWithProviders } from '@/test-utils/render'

import { useRelease } from './use-release'

vi.mock('@/api/releases', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/releases')>()
  return {
    ...actual,
    getReleaseCapabilities: vi.fn(),
    getReleaseSourceInventory: vi.fn(),
    getReleaseJob: vi.fn(),
    getReleaseResult: vi.fn(),
    previewRelease: vi.fn(),
    submitRelease: vi.fn(),
    cancelReleaseJob: vi.fn(),
    recoverReleaseJob: vi.fn(),
    listReleaseJobs: vi.fn(),
    lookupAcceptedRelease: vi.fn(),
  }
})

const DATASET = 'dataset-a'
const JOB_ID = '0123456789abcdef0123456789abcdef'
const SCOPE_A = 'scope-a'
const SCOPE_B = 'scope-b'

function pointerKey(scope: string): string {
  return `release-active-job:v2:${JSON.stringify([DATASET, scope, null])}`
}

function pendingKey(scope: string): string {
  return `release-pending-submission:v1:${JSON.stringify([DATASET, scope, null])}`
}

function inventoryPage(overrides: Partial<releaseApi.ReleaseInventoryPage> = {}) {
  return {
    schema: 'curated-release-api/v1',
    items: [],
    total: 0,
    nextCursor: null,
    ...overrides,
  } satisfies releaseApi.ReleaseInventoryPage
}

function jobStatus(state: releaseApi.ReleaseJobState): releaseApi.ReleaseJobStatus {
  return {
    jobId: JOB_ID,
    state,
    cancelRequested: false,
    effectsStopped: false,
    cleanupState: 'none',
    capacityState: 'ok',
    destinationCurrentBytes: 0,
    destinationPeakBytes: 0,
    schema: 'curated-release-api/v1',
    scratchBytes: 0,
    peakScratchBytes: 0,
    ioCounters: {},
    diagnostics: {},
  }
}

describe('useRelease', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    globalThis.localStorage.clear()
    vi.mocked(releaseApi.getReleaseCapabilities).mockResolvedValue({
      schema: 'curated-release-api/v1',
      jobStore: true,
      planning: true,
      destinations: [],
      operations: [],
      target: { name: 'test', version: '1' },
      routes: [],
      capacity: 'ok',
    })
    vi.mocked(releaseApi.getReleaseSourceInventory).mockResolvedValue(inventoryPage())
    vi.mocked(releaseApi.getReleaseJob).mockResolvedValue(jobStatus('running'))
    vi.mocked(releaseApi.cancelReleaseJob).mockResolvedValue(jobStatus('cancelling'))
    vi.mocked(releaseApi.recoverReleaseJob).mockResolvedValue(jobStatus('running'))
    vi.mocked(releaseApi.listReleaseJobs).mockResolvedValue({ items: [], nextCursor: null })
  })

  it('restores a saved pointer without submitting anything', async () => {
    globalThis.localStorage.setItem(pointerKey(SCOPE_A), JOB_ID)

    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))

    expect(result.current.activeJobId).toBe(JOB_ID)
    await waitFor(() => expect(releaseApi.getReleaseJob).toHaveBeenCalledWith(JOB_ID))
    expect(releaseApi.submitRelease).not.toHaveBeenCalled()
  })

  it('ignores stored values that are not job identifiers', () => {
    globalThis.localStorage.setItem(pointerKey(SCOPE_A), 'not-a-job')

    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))

    expect(result.current.activeJobId).toBeNull()
  })

  it('cancel and recover keep the pointer, while startNew clears it without cancelling', async () => {
    globalThis.localStorage.setItem(pointerKey(SCOPE_A), JOB_ID)
    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))

    await act(async () => {
      await result.current.cancel()
    })
    expect(releaseApi.cancelReleaseJob).toHaveBeenCalledTimes(1)
    expect(releaseApi.cancelReleaseJob).toHaveBeenCalledWith(JOB_ID)
    expect(result.current.activeJobId).toBe(JOB_ID)
    expect(globalThis.localStorage.getItem(pointerKey(SCOPE_A))).toBe(JOB_ID)

    await act(async () => {
      await result.current.recover()
    })
    expect(releaseApi.recoverReleaseJob).toHaveBeenCalledWith(JOB_ID)
    expect(globalThis.localStorage.getItem(pointerKey(SCOPE_A))).toBe(JOB_ID)

    act(() => {
      result.current.startNew()
    })
    expect(result.current.activeJobId).toBeNull()
    expect(globalThis.localStorage.getItem(pointerKey(SCOPE_A))).toBeNull()
    expect(releaseApi.cancelReleaseJob).toHaveBeenCalledTimes(1)
    expect(releaseApi.submitRelease).not.toHaveBeenCalled()
  })

  it('isolates pointers when the scope changes', () => {
    globalThis.localStorage.setItem(pointerKey(SCOPE_A), JOB_ID)
    const { result, rerender } = renderHookWithProviders(
      ({ scope }: { scope: string }) => useRelease(DATASET, scope),
      { initialProps: { scope: SCOPE_A } },
    )
    expect(result.current.activeJobId).toBe(JOB_ID)

    rerender({ scope: SCOPE_B })

    expect(result.current.activeJobId).toBeNull()
  })

  it('rejects a cursor that does not advance', async () => {
    vi.mocked(releaseApi.getReleaseSourceInventory)
      .mockResolvedValueOnce(
        inventoryPage({
          items: [{ episodeIndex: 0 } as releaseApi.ReleaseInventoryItem],
          total: 200,
          nextCursor: 100,
        }),
      )
      .mockResolvedValueOnce(
        inventoryPage({
          items: [{ episodeIndex: 1 } as releaseApi.ReleaseInventoryItem],
          total: 200,
          nextCursor: 100,
        }),
      )

    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))

    await waitFor(() => expect(result.current.inventory.isError).toBe(true))
    expect(result.current.inventory.error?.message).toBe('inventory_cursor_not_advancing')
    expect(result.current.inventoryReady).toBe(false)
  })

  it('rejects a population above the client limit', async () => {
    vi.mocked(releaseApi.getReleaseSourceInventory).mockResolvedValueOnce(
      inventoryPage({
        items: [{ episodeIndex: 0 } as releaseApi.ReleaseInventoryItem],
        total: 10001,
      }),
    )

    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))

    await waitFor(() => expect(result.current.inventory.isError).toBe(true))
    expect(result.current.inventory.error?.message).toBe('inventory_too_large')
  })

  it('rejects duplicate episodes in the inventory', async () => {
    vi.mocked(releaseApi.getReleaseSourceInventory).mockResolvedValueOnce(
      inventoryPage({
        items: [
          { episodeIndex: 0 } as releaseApi.ReleaseInventoryItem,
          { episodeIndex: 0 } as releaseApi.ReleaseInventoryItem,
        ],
        total: 2,
      }),
    )

    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))

    await waitFor(() => expect(result.current.inventory.isError).toBe(true))
    expect(result.current.inventory.error?.message).toBe('inventory_duplicate_episode')
    expect(result.current.inventoryReady).toBe(false)
  })

  it('recovers a lost submission response by lookup after reload without submitting again', async () => {
    globalThis.localStorage.setItem(
      pendingKey(SCOPE_A),
      JSON.stringify({ idempotencyKey: 'idem-key-0001' }),
    )
    vi.mocked(releaseApi.lookupAcceptedRelease).mockResolvedValueOnce({
      jobId: JOB_ID,
      replayed: true,
    })

    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))

    expect(result.current.hasPendingSubmission).toBe(true)
    await waitFor(() => expect(result.current.activeJobId).toBe(JOB_ID))
    expect(releaseApi.lookupAcceptedRelease).toHaveBeenCalledWith('idem-key-0001')
    expect(releaseApi.submitRelease).not.toHaveBeenCalled()
    expect(result.current.hasPendingSubmission).toBe(false)
    expect(globalThis.localStorage.getItem(pendingKey(SCOPE_A))).toBeNull()
    expect(globalThis.localStorage.getItem(pointerKey(SCOPE_A))).toBe(JOB_ID)
  })

  it('lists jobs filtered to the source dataset', async () => {
    renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))

    await waitFor(() =>
      expect(releaseApi.listReleaseJobs).toHaveBeenCalledWith({
        cursor: 0,
        limit: 20,
        datasetId: DATASET,
      }),
    )
  })

  it('selects a listed job without cancelling or submitting', () => {
    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))

    act(() => {
      result.current.selectJob(JOB_ID)
    })

    expect(result.current.activeJobId).toBe(JOB_ID)
    expect(globalThis.localStorage.getItem(pointerKey(SCOPE_A))).toBe(JOB_ID)
    expect(releaseApi.cancelReleaseJob).not.toHaveBeenCalled()
    expect(releaseApi.submitRelease).not.toHaveBeenCalled()
  })

  it('rejects a malformed job identifier when selecting', () => {
    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))

    expect(() => result.current.selectJob('not-a-job')).toThrow('invalid_job_id')
    expect(result.current.activeJobId).toBeNull()
  })

  it('persists the pending key before submission and retains only the accepted pointer', async () => {
    const request = { datasetId: DATASET } as releaseApi.ReleaseRequest
    const submission = { idempotencyKey: 'idempotency-1234' } as releaseApi.ReleaseSubmission
    vi.mocked(releaseApi.previewRelease).mockResolvedValue({ digest: 'preview' } as never)
    vi.mocked(releaseApi.submitRelease).mockImplementation(async () => {
      expect(JSON.parse(localStorage.getItem(pendingKey(SCOPE_A))!)).toEqual({
        idempotencyKey: 'idempotency-1234',
      })
      return { jobId: JOB_ID, replayed: false }
    })
    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))
    await act(async () => {
      expect(await result.current.preview(request)).toEqual({ digest: 'preview' })
      expect(await result.current.submit(request, submission)).toEqual({
        jobId: JOB_ID,
        replayed: false,
      })
    })
    expect(result.current.activeJobId).toBe(JOB_ID)
    expect(result.current.hasPendingSubmission).toBe(false)
    expect(localStorage.getItem(pointerKey(SCOPE_A))).toBe(JOB_ID)
    expect(localStorage.getItem(pendingKey(SCOPE_A))).toBeNull()
    expect(releaseApi.lookupAcceptedRelease).not.toHaveBeenCalled()
  })

  it.each([
    [new releaseApi.ReleaseRequestError('stale', 'stale_preview', 409), false],
    [new Error('connection lost'), true],
  ])('retains recovery data only when acceptance is uncertain', async (error, retained) => {
    vi.mocked(releaseApi.submitRelease).mockRejectedValue(error)
    vi.mocked(releaseApi.lookupAcceptedRelease).mockRejectedValue(
      new releaseApi.ReleaseRequestError('not found', 'request_not_found', 404),
    )
    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))
    await act(async () => {
      await expect(
        result.current.submit(
          { datasetId: DATASET } as releaseApi.ReleaseRequest,
          { idempotencyKey: 'idempotency-1234' } as releaseApi.ReleaseSubmission,
        ),
      ).rejects.toBe(error)
    })
    expect(result.current.hasPendingSubmission).toBe(retained)
    expect(localStorage.getItem(pendingKey(SCOPE_A)) !== null).toBe(retained)
    expect(result.current.activeJobId).toBeNull()
  })

  it('rejects lifecycle commands without an active job', async () => {
    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))
    await expect(result.current.cancel()).rejects.toThrow('no_active_job')
    await expect(result.current.recover()).rejects.toThrow('no_active_job')
    expect(releaseApi.cancelReleaseJob).not.toHaveBeenCalled()
    expect(releaseApi.recoverReleaseJob).not.toHaveBeenCalled()
  })

  it('loads a verified result only after committed status', async () => {
    localStorage.setItem(pointerKey(SCOPE_A), JOB_ID)
    vi.mocked(releaseApi.getReleaseJob).mockResolvedValue(jobStatus('committed'))
    vi.mocked(releaseApi.getReleaseResult).mockResolvedValue({
      jobId: JOB_ID,
      artifacts: ['manifest'],
    } as never)
    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))
    await waitFor(() => expect(result.current.result.isSuccess).toBe(true))
    expect(result.current.result.data?.artifacts).toEqual(['manifest'])
    expect(releaseApi.getReleaseResult).toHaveBeenCalledWith(JOB_ID)
  })
})
