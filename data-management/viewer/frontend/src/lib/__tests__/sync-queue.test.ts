import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const apiClientMocks = vi.hoisted(() => ({
  mutationHeaders: vi.fn(async () => ({ 'X-CSRF-Token': 'test-token' })),
  handleResponse: vi.fn(async () => ({})),
}))

const offlineStorageMocks = vi.hoisted(() => ({
  getPendingSyncItems: vi.fn(),
  removeSyncItem: vi.fn(async () => undefined),
  updateAnnotationSyncStatus: vi.fn(async () => undefined),
  updateSyncItemRetry: vi.fn(async () => undefined),
}))

vi.mock('@/lib/api-client', () => apiClientMocks)
vi.mock('../offline-storage', () => offlineStorageMocks)

import {
  isOnline,
  processSyncQueue,
  type SyncQueueItem,
  SyncQueueManager,
  syncQueueManager,
  waitForOnline,
} from '../sync-queue'

function setOnline(value: boolean): void {
  Object.defineProperty(navigator, 'onLine', { configurable: true, value })
}

function makeItem(overrides: Partial<SyncQueueItem> = {}): SyncQueueItem {
  return {
    id: 'item-1',
    type: 'create',
    datasetId: 'ds-1',
    episodeId: 'ep-1',
    annotationId: 'ann-1',
    payload: { foo: 'bar' },
    createdAt: '2024-01-01T00:00:00Z',
    retryCount: 0,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  setOnline(true)
  apiClientMocks.mutationHeaders.mockResolvedValue({ 'X-CSRF-Token': 'test-token' })
  apiClientMocks.handleResponse.mockResolvedValue({})
  offlineStorageMocks.getPendingSyncItems.mockResolvedValue([])
  offlineStorageMocks.removeSyncItem.mockResolvedValue(undefined)
  offlineStorageMocks.updateAnnotationSyncStatus.mockResolvedValue(undefined)
  offlineStorageMocks.updateSyncItemRetry.mockResolvedValue(undefined)
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })))
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  setOnline(true)
})

describe('isOnline', () => {
  it('reflects navigator.onLine when true', () => {
    setOnline(true)
    expect(isOnline()).toBe(true)
  })

  it('reflects navigator.onLine when false', () => {
    setOnline(false)
    expect(isOnline()).toBe(false)
  })
})

describe('waitForOnline', () => {
  it('resolves immediately when already online', async () => {
    setOnline(true)
    await expect(waitForOnline()).resolves.toBeUndefined()
  })

  it('resolves when an "online" event fires', async () => {
    setOnline(false)
    const pending = waitForOnline()
    setOnline(true)
    window.dispatchEvent(new Event('online'))
    await expect(pending).resolves.toBeUndefined()
  })
})

describe('processSyncQueue', () => {
  it('returns an empty success result when offline', async () => {
    setOnline(false)
    const result = await processSyncQueue()
    expect(result).toEqual({ success: true, syncedCount: 0, failedCount: 0, errors: [] })
    expect(offlineStorageMocks.getPendingSyncItems).not.toHaveBeenCalled()
  })

  it('returns success when there are no pending items', async () => {
    offlineStorageMocks.getPendingSyncItems.mockResolvedValueOnce([])
    const result = await processSyncQueue()
    expect(result.success).toBe(true)
    expect(result.syncedCount).toBe(0)
  })

  it('POSTs create items with mutation headers and removes them on success', async () => {
    offlineStorageMocks.getPendingSyncItems.mockResolvedValueOnce([makeItem({ type: 'create' })])
    vi.useFakeTimers()

    const promise = processSyncQueue()
    await vi.runAllTimersAsync()
    const result = await promise

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/datasets/ds-1/episodes/ep-1/annotations',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ foo: 'bar' }),
        headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
      }),
    )
    expect(offlineStorageMocks.updateAnnotationSyncStatus).toHaveBeenCalledWith(
      'ann-1',
      'synced',
      expect.any(String),
    )
    expect(offlineStorageMocks.removeSyncItem).toHaveBeenCalledWith('item-1')
    expect(result).toMatchObject({ success: true, syncedCount: 1, failedCount: 0 })
  })

  it('PUTs update items to /api/annotations/:id', async () => {
    offlineStorageMocks.getPendingSyncItems.mockResolvedValueOnce([
      makeItem({ type: 'update', annotationId: 'ann-9' }),
    ])
    vi.useFakeTimers()

    const promise = processSyncQueue()
    await vi.runAllTimersAsync()
    await promise

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/annotations/ann-9',
      expect.objectContaining({ method: 'PUT' }),
    )
  })

  it('DELETEs delete items to /api/annotations/:id', async () => {
    offlineStorageMocks.getPendingSyncItems.mockResolvedValueOnce([
      makeItem({ type: 'delete', annotationId: 'ann-9' }),
    ])
    vi.useFakeTimers()

    const promise = processSyncQueue()
    await vi.runAllTimersAsync()
    await promise

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/annotations/ann-9',
      expect.objectContaining({ method: 'DELETE' }),
    )
  })

  it('marks annotation as conflict and removes the item on 409', async () => {
    offlineStorageMocks.getPendingSyncItems.mockResolvedValueOnce([makeItem()])
    apiClientMocks.handleResponse.mockRejectedValueOnce(
      Object.assign(new Error('conflict'), { status: 409 }),
    )
    vi.useFakeTimers()

    const promise = processSyncQueue()
    await vi.runAllTimersAsync()
    const result = await promise

    expect(offlineStorageMocks.updateAnnotationSyncStatus).toHaveBeenCalledWith('ann-1', 'conflict')
    expect(offlineStorageMocks.removeSyncItem).toHaveBeenCalledWith('item-1')
    expect(offlineStorageMocks.updateSyncItemRetry).not.toHaveBeenCalled()
    expect(result).toMatchObject({ success: false, syncedCount: 0, failedCount: 1 })
  })

  it('updates retry count on generic errors', async () => {
    offlineStorageMocks.getPendingSyncItems.mockResolvedValueOnce([
      makeItem({ lastError: 'previous failure' }),
    ])
    apiClientMocks.handleResponse.mockRejectedValueOnce(new Error('network down'))
    vi.useFakeTimers()

    const promise = processSyncQueue()
    await vi.runAllTimersAsync()
    const result = await promise

    expect(offlineStorageMocks.updateSyncItemRetry).toHaveBeenCalledWith('item-1', 'network down')
    expect(offlineStorageMocks.removeSyncItem).not.toHaveBeenCalled()
    expect(result.failedCount).toBe(1)
    expect(result.errors).toEqual([{ id: 'item-1', error: 'previous failure' }])
  })

  it('skips items past the retry limit without calling fetch', async () => {
    offlineStorageMocks.getPendingSyncItems.mockResolvedValueOnce([
      makeItem({ retryCount: 3, lastError: 'boom' }),
    ])

    const result = await processSyncQueue()

    expect(global.fetch).not.toHaveBeenCalled()
    expect(result.failedCount).toBe(1)
    expect(result.errors[0]).toEqual({ id: 'item-1', error: 'Exceeded max retries: boom' })
  })
})

describe('SyncQueueManager', () => {
  it('process() returns a no-op result when already processing', async () => {
    const manager = new SyncQueueManager()
    let release: () => void = () => undefined
    offlineStorageMocks.getPendingSyncItems.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = () => resolve([])
        }),
    )

    const first = manager.process()
    const second = await manager.process()
    expect(second).toEqual({ success: true, syncedCount: 0, failedCount: 0, errors: [] })

    release()
    await first
  })

  it('notifies listeners on each process call', async () => {
    const manager = new SyncQueueManager()
    const listener = vi.fn()
    manager.addListener(listener)

    await manager.process()

    expect(listener).toHaveBeenCalledTimes(1)
    expect(listener).toHaveBeenCalledWith(
      expect.objectContaining({ success: true, syncedCount: 0 }),
    )
  })

  it('addListener returns an unsubscribe function', async () => {
    const manager = new SyncQueueManager()
    const listener = vi.fn()
    const unsubscribe = manager.addListener(listener)
    unsubscribe()

    await manager.process()

    expect(listener).not.toHaveBeenCalled()
  })

  it('swallows listener errors so other listeners still run', async () => {
    const manager = new SyncQueueManager()
    const failing = vi.fn(() => {
      throw new Error('listener crash')
    })
    const ok = vi.fn()
    manager.addListener(failing)
    manager.addListener(ok)

    await expect(manager.process()).resolves.toBeDefined()
    expect(failing).toHaveBeenCalled()
    expect(ok).toHaveBeenCalled()
  })

  it('start() schedules repeated processing and stop() cancels it', async () => {
    vi.useFakeTimers()
    const manager = new SyncQueueManager()
    const processSpy = vi.spyOn(manager, 'process').mockResolvedValue({
      success: true,
      syncedCount: 0,
      failedCount: 0,
      errors: [],
    })

    manager.start(5000)
    expect(processSpy).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(5000)
    expect(processSpy).toHaveBeenCalledTimes(2)

    manager.stop()
    await vi.advanceTimersByTimeAsync(5000)
    expect(processSpy).toHaveBeenCalledTimes(2)
  })

  it('start() is idempotent while an interval is active', () => {
    vi.useFakeTimers()
    const manager = new SyncQueueManager()
    const processSpy = vi.spyOn(manager, 'process').mockResolvedValue({
      success: true,
      syncedCount: 0,
      failedCount: 0,
      errors: [],
    })

    manager.start(5000)
    manager.start(5000)
    expect(processSpy).toHaveBeenCalledTimes(1)
    manager.stop()
  })

  it('processes the queue shortly after an "online" event', async () => {
    vi.useFakeTimers()
    const manager = new SyncQueueManager()
    const processSpy = vi.spyOn(manager, 'process').mockResolvedValue({
      success: true,
      syncedCount: 0,
      failedCount: 0,
      errors: [],
    })

    manager.start(60000)
    processSpy.mockClear()

    window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(1000)

    expect(processSpy).toHaveBeenCalledTimes(1)
    manager.stop()
  })

  it('exports a shared singleton instance', () => {
    expect(syncQueueManager).toBeInstanceOf(SyncQueueManager)
  })
})
