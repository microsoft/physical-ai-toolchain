import { focusManager } from '@tanstack/react-query'
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
    getReleaseSourceInventoryDetails: vi.fn(),
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
    recoveryRequired: false,
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

  it('keeps late-arriving job history aligned with live status without resubmitting or cancelling', async () => {
    globalThis.localStorage.setItem(pointerKey(SCOPE_A), JOB_ID)
    const createdAt = '2026-11-05T12:18:17.566Z'
    const otherJob = {
      ...jobStatus('accepted'),
      jobId: 'f'.repeat(32),
      datasetId: DATASET,
      createdAt,
    }
    let finishHistory!: (page: releaseApi.ReleaseJobPage) => void
    vi.mocked(releaseApi.listReleaseJobs).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishHistory = resolve
        }),
    )
    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))
    await waitFor(() => expect(result.current.status.data?.state).toBe('running'))
    await act(async () =>
      finishHistory({
        items: [{ ...jobStatus('accepted'), datasetId: DATASET, createdAt }, otherJob],
        nextCursor: null,
      }),
    )
    await waitFor(() => expect(result.current.jobs.data?.pages[0].items[0].state).toBe('running'))

    vi.mocked(releaseApi.getReleaseJob).mockResolvedValue({
      ...jobStatus('running'),
      recoveryRequired: true,
    })
    await act(async () => {
      await result.current.status.refetch()
    })
    await waitFor(() =>
      expect(result.current.jobs.data?.pages[0].items[0].recoveryRequired).toBe(true),
    )

    vi.mocked(releaseApi.getReleaseJob).mockResolvedValue(jobStatus('sealed'))
    await act(async () => {
      await result.current.status.refetch()
    })
    await waitFor(() => expect(result.current.jobs.data?.pages[0].items[0].state).toBe('sealed'))
    expect(result.current.jobs.data?.pages[0].items[0].createdAt).toBe(createdAt)
    expect(result.current.jobs.data?.pages[0].items[1]).toEqual(otherJob)
    act(() => result.current.startNew())
    expect(result.current.jobs.data?.pages[0].items[0].state).toBe('sealed')
    expect(releaseApi.listReleaseJobs).toHaveBeenCalledTimes(1)
    expect(releaseApi.submitRelease).not.toHaveBeenCalled()
    expect(releaseApi.cancelReleaseJob).not.toHaveBeenCalled()
  })

  it('loads details sequentially in bounded batches and deduplicates queued episodes', async () => {
    const items = Array.from({ length: 41 }, (_, episodeIndex) => ({
      episodeIndex,
      detailed: false,
      annotation: null,
      edits: null,
      labels: null,
      provenance: null,
      judge: null,
      resources: [],
    }))
    vi.mocked(releaseApi.getReleaseSourceInventory).mockResolvedValue(
      inventoryPage({ items, total: items.length }),
    )
    let finish!: (page: releaseApi.ReleaseInventoryDetailsPage) => void
    vi.mocked(releaseApi.getReleaseSourceInventoryDetails)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve
          }),
      )
      .mockImplementation(async (_dataset, indices) => ({
        schema: 'curated-release-api/v1',
        items: indices.map((index) => ({ ...items[index], detailed: true })),
      }))
    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))
    await waitFor(() => expect(result.current.inventoryReady).toBe(true))
    act(() => {
      result.current.requestDetails(items.map((item) => item.episodeIndex))
      result.current.requestDetails([0, 20, 40])
    })
    await waitFor(() =>
      expect(releaseApi.getReleaseSourceInventoryDetails).toHaveBeenCalledTimes(1),
    )
    expect(vi.mocked(releaseApi.getReleaseSourceInventoryDetails).mock.calls[0][1]).toHaveLength(20)
    await act(async () =>
      finish({
        schema: 'curated-release-api/v1',
        items: items.slice(0, 20).map((item) => ({ ...item, detailed: true })),
      }),
    )
    await waitFor(() =>
      expect(result.current.inventoryItems.every((item) => item.detailed)).toBe(true),
    )
    expect(
      vi
        .mocked(releaseApi.getReleaseSourceInventoryDetails)
        .mock.calls.map((call) => call[1].length),
    ).toEqual([20, 20, 1])
  })

  it.each(['source', 'principal', 'close'])(
    'aborts detail work and drops queued batches on %s changes',
    async (changed) => {
      const items = Array.from({ length: 21 }, (_, episodeIndex) => ({
        episodeIndex,
        detailed: false,
        annotation: null,
        edits: null,
        labels: null,
        provenance: null,
        judge: null,
        resources: [],
      }))
      vi.mocked(releaseApi.getReleaseSourceInventory).mockResolvedValue(
        inventoryPage({ items, total: items.length }),
      )
      let finish!: (page: releaseApi.ReleaseInventoryDetailsPage) => void
      vi.mocked(releaseApi.getReleaseSourceInventoryDetails).mockImplementation(
        () =>
          new Promise((resolve) => {
            finish = resolve
          }),
      )
      const { result, rerender } = renderHookWithProviders(
        ({ scope, source }) => useRelease(DATASET, scope, source),
        { initialProps: { scope: SCOPE_A, source: 'source-a' } },
      )
      await waitFor(() => expect(result.current.inventoryReady).toBe(true))
      act(() => result.current.requestDetails(items.map((item) => item.episodeIndex)))
      await waitFor(() =>
        expect(releaseApi.getReleaseSourceInventoryDetails).toHaveBeenCalledTimes(1),
      )
      const signal = vi.mocked(releaseApi.getReleaseSourceInventoryDetails).mock.calls[0][2]
      if (changed === 'close') {
        act(() => result.current.cancelPreparation())
      } else {
        rerender({
          scope: changed === 'principal' ? SCOPE_B : SCOPE_A,
          source: changed === 'source' ? 'source-b' : 'source-a',
        })
      }
      expect(signal?.aborted).toBe(true)
      await act(async () =>
        finish({
          schema: 'curated-release-api/v1',
          items: items.map((item) => ({ ...item, detailed: true })),
        }),
      )
      await waitFor(() => expect(result.current.inventoryReady).toBe(true))
      expect(result.current.inventoryItems.every((item) => !item.detailed)).toBe(true)
      expect(releaseApi.getReleaseSourceInventoryDetails).toHaveBeenCalledTimes(1)
      expect(releaseApi.cancelReleaseJob).not.toHaveBeenCalled()
    },
  )

  it('ignores stored values that are not job identifiers', () => {
    globalThis.localStorage.setItem(pointerKey(SCOPE_A), 'not-a-job')

    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))

    expect(result.current.activeJobId).toBeNull()
  })

  it.each(['{', 'null', '[]', '{"idempotencyKey":3}', '{"idempotencyKey":"bad"}'])(
    'ignores malformed pending submission storage: %s',
    (value) => {
      localStorage.setItem(pendingKey(SCOPE_A), value)
      const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))
      expect(result.current.hasPendingSubmission).toBe(false)
      expect(releaseApi.lookupAcceptedRelease).not.toHaveBeenCalled()
    },
  )

  it('keeps in-memory job controls usable when browser storage cannot be read', () => {
    vi.stubGlobal('localStorage', {
      getItem: vi.fn(() => {
        throw new DOMException('Storage denied', 'SecurityError')
      }),
      setItem: vi.fn(),
    })
    try {
      const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))
      expect(result.current.activeJobId).toBeNull()
      expect(result.current.hasPendingSubmission).toBe(false)
      act(() => result.current.selectJob(JOB_ID))
      expect(result.current.activeJobId).toBe(JOB_ID)
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it.each([
    [
      'inventory_total_changed',
      [
        inventoryPage({
          total: 2,
          items: [{ episodeIndex: 0 } as releaseApi.ReleaseInventoryItem],
          nextCursor: 1,
        }),
        inventoryPage({ total: 3 }),
      ],
    ],
    ['inventory_empty_page', [inventoryPage({ total: 2, nextCursor: 1 })]],
    [
      'inventory_overflow',
      [
        inventoryPage({
          total: 0,
          items: [{ episodeIndex: 0 } as releaseApi.ReleaseInventoryItem],
        }),
      ],
    ],
    ['inventory_incomplete', [inventoryPage({ total: 1 })]],
  ] as const)('refuses inconsistent inventory: %s', async (message, pages) => {
    const inventory = vi.mocked(releaseApi.getReleaseSourceInventory)
    for (const page of pages) inventory.mockResolvedValueOnce(page)
    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))
    await waitFor(() => expect(result.current.inventory.isError).toBe(true))
    expect(result.current.inventory.error?.message).toBe(message)
    expect(result.current.inventoryReady).toBe(false)
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

  it('exposes partial progress without marking inventory ready, then completes', async () => {
    let finish!: (page: releaseApi.ReleaseInventoryPage) => void
    vi.mocked(releaseApi.getReleaseSourceInventory)
      .mockResolvedValueOnce(
        inventoryPage({
          total: 2,
          items: [{ episodeIndex: 0 } as releaseApi.ReleaseInventoryItem],
          nextCursor: 1,
        }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve
          }),
      )
    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))

    await waitFor(() => expect(result.current.inventoryProgress).toEqual({ loaded: 1, total: 2 }))
    expect(result.current.inventoryItems.map((item) => item.episodeIndex)).toEqual([0])
    expect(result.current.inventoryReady).toBe(false)
    try {
      await act(async () => {
        focusManager.setFocused(false)
        focusManager.setFocused(true)
      })
      expect(releaseApi.getReleaseSourceInventory).toHaveBeenCalledTimes(2)
      expect(result.current.inventoryProgress).toEqual({ loaded: 1, total: 2 })
      expect(vi.mocked(releaseApi.getReleaseSourceInventory).mock.calls[1][2]?.aborted).toBe(false)
    } finally {
      focusManager.setFocused(undefined)
    }
    await act(async () =>
      finish(
        inventoryPage({
          total: 2,
          items: [{ episodeIndex: 1 } as releaseApi.ReleaseInventoryItem],
        }),
      ),
    )
    await waitFor(() => expect(result.current.inventoryReady).toBe(true))
    expect(result.current.inventoryItems.map((item) => item.episodeIndex)).toEqual([0, 1])
    expect(result.current.inventoryProgress).toEqual({ loaded: 2, total: 2 })
  })

  it('keeps stale inventory on window focus while allowing explicit refresh and invalidation', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    const { result, queryClient, unmount } = renderHookWithProviders(() =>
      useRelease(DATASET, SCOPE_A),
    )
    try {
      await waitFor(() => expect(result.current.inventoryReady).toBe(true))
      expect(releaseApi.getReleaseSourceInventory).toHaveBeenCalledTimes(1)

      await act(async () => {
        focusManager.setFocused(false)
        vi.setSystemTime(Date.now() + 61_000)
        focusManager.setFocused(true)
      })
      await waitFor(() => expect(releaseApi.getReleaseCapabilities).toHaveBeenCalledTimes(2))
      expect(releaseApi.getReleaseSourceInventory).toHaveBeenCalledTimes(1)
      expect(result.current.inventoryReady).toBe(true)

      await act(async () => {
        await result.current.inventory.refetch()
      })
      expect(releaseApi.getReleaseSourceInventory).toHaveBeenCalledTimes(2)

      await act(async () => {
        await queryClient.invalidateQueries({
          queryKey: ['releases', 'inventory', SCOPE_A, DATASET, null],
        })
      })
      expect(releaseApi.getReleaseSourceInventory).toHaveBeenCalledTimes(3)
      await waitFor(() => expect(result.current.inventoryReady).toBe(true))
    } finally {
      unmount()
      focusManager.setFocused(undefined)
      vi.useRealTimers()
    }
  })

  it('retains partial results on failure and explicitly retries from the start', async () => {
    const first = inventoryPage({
      total: 2,
      items: [{ episodeIndex: 0 } as releaseApi.ReleaseInventoryItem],
      nextCursor: 1,
    })
    vi.mocked(releaseApi.getReleaseSourceInventory)
      .mockResolvedValueOnce(first)
      .mockRejectedValueOnce(
        new releaseApi.ReleaseRequestError('Timed out', 'release_operation_timed_out', 504),
      )
    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))
    await waitFor(() => expect(result.current.inventory.isError).toBe(true))
    expect(result.current.inventoryItems).toEqual(first.items)
    expect(result.current.inventoryReady).toBe(false)
    expect(releaseApi.getReleaseSourceInventory).toHaveBeenCalledTimes(2)

    vi.mocked(releaseApi.getReleaseSourceInventory).mockResolvedValueOnce(
      inventoryPage({ total: 1, items: first.items }),
    )
    await act(async () => {
      await result.current.inventory.refetch()
    })
    expect(result.current.inventoryReady).toBe(true)
    expect(releaseApi.getReleaseSourceInventory).toHaveBeenLastCalledWith(
      DATASET,
      { cursor: 0, limit: 100 },
      expect.any(AbortSignal),
    )
  })

  it.each(['principal', 'source'])(
    'cancels and hides old progress when the %s changes',
    async (changed) => {
      let finish!: (page: releaseApi.ReleaseInventoryPage) => void
      vi.mocked(releaseApi.getReleaseSourceInventory)
        .mockResolvedValueOnce(
          inventoryPage({
            total: 2,
            items: [{ episodeIndex: 0 } as releaseApi.ReleaseInventoryItem],
            nextCursor: 1,
          }),
        )
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              finish = resolve
            }),
        )
        .mockImplementationOnce(() => new Promise(() => {}))
      const { result, rerender } = renderHookWithProviders(
        ({ scope, source }) => useRelease(DATASET, scope, source),
        { initialProps: { scope: SCOPE_A, source: 'source-a' } },
      )
      await waitFor(() => expect(result.current.inventoryProgress.loaded).toBe(1))
      const previousSignal = vi.mocked(releaseApi.getReleaseSourceInventory).mock.calls[1][2]
      rerender({
        scope: changed === 'principal' ? SCOPE_B : SCOPE_A,
        source: changed === 'source' ? 'source-b' : 'source-a',
      })
      expect(previousSignal?.aborted).toBe(true)
      expect(result.current.inventoryItems).toEqual([])
      await act(async () =>
        finish(
          inventoryPage({
            total: 2,
            items: [{ episodeIndex: 1 } as releaseApi.ReleaseInventoryItem],
          }),
        ),
      )
      expect(result.current.inventoryProgress).toEqual({ loaded: 0, total: null })
      expect(result.current.inventoryReady).toBe(false)
    },
  )

  it('observes scoped active pointer updates from another tab without submitting or cancelling', async () => {
    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))
    act(() => result.current.startNew())

    act(() => {
      localStorage.setItem(pointerKey(SCOPE_A), JOB_ID)
      window.dispatchEvent(new StorageEvent('storage', { key: pointerKey(SCOPE_A) }))
    })
    expect(result.current.activeJobId).toBe(JOB_ID)
    act(() => {
      localStorage.setItem(pointerKey(SCOPE_B), 'b'.repeat(32))
      window.dispatchEvent(new StorageEvent('storage', { key: pointerKey(SCOPE_B) }))
    })
    expect(result.current.activeJobId).toBe(JOB_ID)
    act(() => {
      localStorage.removeItem(pointerKey(SCOPE_A))
      window.dispatchEvent(new StorageEvent('storage', { key: pointerKey(SCOPE_A) }))
    })
    expect(result.current.activeJobId).toBeNull()
    expect(releaseApi.submitRelease).not.toHaveBeenCalled()
    expect(releaseApi.cancelReleaseJob).not.toHaveBeenCalled()
  })

  it('resolves a pending key from another tab through lookup only', async () => {
    vi.mocked(releaseApi.lookupAcceptedRelease).mockResolvedValue({ jobId: JOB_ID, replayed: true })
    const { result } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))
    act(() => result.current.startNew())

    act(() => {
      localStorage.setItem(pendingKey(SCOPE_A), JSON.stringify({ idempotencyKey: 'other-tab-key' }))
      window.dispatchEvent(new StorageEvent('storage', { key: pendingKey(SCOPE_A) }))
    })

    await waitFor(() => expect(result.current.activeJobId).toBe(JOB_ID))
    expect(releaseApi.lookupAcceptedRelease).toHaveBeenCalledWith('other-tab-key')
    expect(releaseApi.submitRelease).not.toHaveBeenCalled()
    expect(releaseApi.cancelReleaseJob).not.toHaveBeenCalled()
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
    const submission = { idempotencyKey: 'test-key' } as releaseApi.ReleaseSubmission
    vi.mocked(releaseApi.previewRelease).mockResolvedValue({ digest: 'preview' } as never)
    vi.mocked(releaseApi.submitRelease).mockImplementation(async () => {
      expect(JSON.parse(localStorage.getItem(pendingKey(SCOPE_A))!)).toEqual({
        idempotencyKey: 'test-key',
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

  it('aborts only the transport on unmount, sends no cancellation, and keeps the pending key', async () => {
    vi.mocked(releaseApi.submitRelease).mockImplementation(
      (_request, _submission, signal) =>
        new Promise((_resolve, reject) => {
          signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
        }),
    )
    const { result, unmount } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))
    let failure: unknown
    const pending = result.current
      .submit(
        { datasetId: DATASET } as releaseApi.ReleaseRequest,
        { idempotencyKey: 'test-key' } as releaseApi.ReleaseSubmission,
      )
      .catch((error: unknown) => {
        failure = error
      })
    await waitFor(() => expect(releaseApi.submitRelease).toHaveBeenCalled())
    const signal = vi.mocked(releaseApi.submitRelease).mock.calls[0][2]

    unmount()
    await pending

    expect(signal?.aborted).toBe(true)
    expect(failure).toMatchObject({ name: 'AbortError' })
    expect(releaseApi.cancelReleaseJob).not.toHaveBeenCalled()
    expect(globalThis.localStorage.getItem(pendingKey(SCOPE_A))).not.toBeNull()
  })

  it.each([
    [new releaseApi.ReleaseRequestError('stale', 'stale_preview', 409), false],
    [
      new releaseApi.ReleaseRequestError(
        'capacity',
        'insufficient capacity for the reserved estimate',
        507,
      ),
      false,
    ],
    [new releaseApi.ReleaseRequestError('unknown capacity failure', 'unknown', 507), true],
    [new releaseApi.ReleaseRequestError('unavailable', 'unavailable', 503), true],
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
          { idempotencyKey: 'test-key' } as releaseApi.ReleaseSubmission,
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

  it('clears only the matching pointer and history entry when the job is not found, then stops polling', async () => {
    const otherJob = 'd'.repeat(32)
    vi.mocked(releaseApi.listReleaseJobs)
      .mockResolvedValueOnce({
        items: [
          { jobId: JOB_ID, state: 'running' },
          { jobId: otherJob, state: 'running' },
        ],
        nextCursor: null,
      } as never)
      .mockResolvedValue({
        items: [{ jobId: otherJob, state: 'running' }],
        nextCursor: null,
      } as never)
    vi.mocked(releaseApi.getReleaseJob).mockRejectedValue(
      new releaseApi.ReleaseRequestError(
        'Release request failed: release job was not found',
        'release job was not found',
        404,
      ),
    )
    globalThis.localStorage.setItem(pointerKey(SCOPE_A), JOB_ID)
    globalThis.localStorage.setItem(pointerKey(SCOPE_B), otherJob)

    const { result, queryClient } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))

    await waitFor(() => expect(result.current.activeJobId).toBeNull())
    expect(globalThis.localStorage.getItem(pointerKey(SCOPE_A))).toBeNull()
    expect(globalThis.localStorage.getItem(pointerKey(SCOPE_B))).toBe(otherJob)
    await waitFor(() => {
      const history = queryClient.getQueryData<{ pages: { items: { jobId: string }[] }[] }>([
        'releases',
        'jobs',
        SCOPE_A,
        DATASET,
      ])
      expect(history?.pages.flatMap((page) => page.items.map((item) => item.jobId))).toEqual([
        otherJob,
      ])
    })
    expect(releaseApi.getReleaseJob).toHaveBeenCalledTimes(1)
    expect(releaseApi.cancelReleaseJob).not.toHaveBeenCalled()
    expect(releaseApi.submitRelease).not.toHaveBeenCalled()
  })

  it('keeps the saved job and its history entry when the status request is unavailable', async () => {
    globalThis.localStorage.setItem(pointerKey(SCOPE_A), JOB_ID)
    vi.mocked(releaseApi.listReleaseJobs).mockResolvedValue({
      items: [{ jobId: JOB_ID, state: 'running' }],
      nextCursor: null,
    } as never)
    vi.mocked(releaseApi.getReleaseJob).mockRejectedValue(
      new releaseApi.ReleaseRequestError('unavailable', 'unavailable', 503),
    )

    const { result, queryClient } = renderHookWithProviders(() => useRelease(DATASET, SCOPE_A))

    await waitFor(() => expect(releaseApi.getReleaseJob).toHaveBeenCalledWith(JOB_ID))
    await waitFor(() => expect(result.current.status.isError).toBe(true))
    expect(result.current.activeJobId).toBe(JOB_ID)
    expect(globalThis.localStorage.getItem(pointerKey(SCOPE_A))).toBe(JOB_ID)
    const history = queryClient.getQueryData<{ pages: { items: { jobId: string }[] }[] }>([
      'releases',
      'jobs',
      SCOPE_A,
      DATASET,
    ])
    expect(history?.pages.flatMap((page) => page.items.map((item) => item.jobId))).toEqual([JOB_ID])
  })
})
