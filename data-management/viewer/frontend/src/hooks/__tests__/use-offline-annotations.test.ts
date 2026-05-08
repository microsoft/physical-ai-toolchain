/**
 * Tests for useOfflineAnnotations hook.
 *
 * Covers IndexedDB-backed local persistence, online/offline event handling,
 * pending-count refresh, sync queue listener registration/cleanup, and
 * online-triggered immediate sync on save.
 */

import { act, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useOfflineAnnotations } from '@/hooks/use-offline-annotations'
import { renderHookWithProviders } from '@/test-utils/render-hook'

const {
  mockSaveAnnotationLocal,
  mockGetAnnotationLocal,
  mockGetAnnotationsBySyncStatus,
  mockDeleteAnnotationLocal,
  mockAddToSyncQueue,
  mockIsOnline,
  mockSyncManager,
} = vi.hoisted(() => ({
  mockSaveAnnotationLocal: vi.fn(),
  mockGetAnnotationLocal: vi.fn(),
  mockGetAnnotationsBySyncStatus: vi.fn(),
  mockDeleteAnnotationLocal: vi.fn(),
  mockAddToSyncQueue: vi.fn(),
  mockIsOnline: vi.fn(),
  mockSyncManager: {
    addListener: vi.fn(),
    process: vi.fn(),
    start: vi.fn(),
    stop: vi.fn(),
  },
}))

vi.mock('@/lib/offline-storage', () => ({
  saveAnnotationLocal: mockSaveAnnotationLocal,
  getAnnotationLocal: mockGetAnnotationLocal,
  getAnnotationsBySyncStatus: mockGetAnnotationsBySyncStatus,
  deleteAnnotationLocal: mockDeleteAnnotationLocal,
  addToSyncQueue: mockAddToSyncQueue,
}))

vi.mock('@/lib/sync-queue', () => ({
  isOnline: mockIsOnline,
  syncQueueManager: mockSyncManager,
}))

describe('useOfflineAnnotations', () => {
  beforeEach(() => {
    mockSaveAnnotationLocal.mockReset().mockResolvedValue(undefined)
    mockGetAnnotationLocal.mockReset().mockResolvedValue(undefined)
    mockGetAnnotationsBySyncStatus.mockReset().mockResolvedValue([])
    mockDeleteAnnotationLocal.mockReset().mockResolvedValue(undefined)
    mockAddToSyncQueue.mockReset().mockResolvedValue(undefined)
    mockIsOnline.mockReset().mockReturnValue(true)
    mockSyncManager.addListener.mockReset().mockReturnValue(() => {})
    mockSyncManager.process.mockReset().mockResolvedValue({
      success: true,
      syncedCount: 0,
      failedCount: 0,
    })
    mockSyncManager.start.mockReset()
    mockSyncManager.stop.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('initializes with current online state and zero pending count', async () => {
    mockIsOnline.mockReturnValue(true)
    const { result } = renderHookWithProviders(() => useOfflineAnnotations())

    expect(result.current.isOnline).toBe(true)
    expect(result.current.isSyncing).toBe(false)
    expect(result.current.lastSyncResult).toBeNull()
    await waitFor(() => {
      expect(mockGetAnnotationsBySyncStatus).toHaveBeenCalledWith('pending')
    })
  })

  it('reflects pending count from storage', async () => {
    mockGetAnnotationsBySyncStatus.mockResolvedValue([{ id: 'a' }, { id: 'b' }, { id: 'c' }])
    const { result } = renderHookWithProviders(() => useOfflineAnnotations())

    await waitFor(() => {
      expect(result.current.pendingCount).toBe(3)
    })
  })

  it('updates online state in response to window events', async () => {
    mockIsOnline.mockReturnValue(true)
    const { result } = renderHookWithProviders(() => useOfflineAnnotations())

    expect(result.current.isOnline).toBe(true)

    act(() => {
      window.dispatchEvent(new Event('offline'))
    })
    await waitFor(() => expect(result.current.isOnline).toBe(false))

    act(() => {
      window.dispatchEvent(new Event('online'))
    })
    await waitFor(() => expect(result.current.isOnline).toBe(true))
  })

  it('removes window listeners on unmount', () => {
    const removeSpy = vi.spyOn(window, 'removeEventListener')
    const { unmount } = renderHookWithProviders(() => useOfflineAnnotations())

    unmount()

    expect(removeSpy).toHaveBeenCalledWith('online', expect.any(Function))
    expect(removeSpy).toHaveBeenCalledWith('offline', expect.any(Function))
  })

  it('subscribes to syncQueueManager and unsubscribes on unmount', () => {
    const unsubscribe = vi.fn()
    mockSyncManager.addListener.mockReturnValue(unsubscribe)

    const { unmount } = renderHookWithProviders(() => useOfflineAnnotations())

    expect(mockSyncManager.addListener).toHaveBeenCalledTimes(1)

    unmount()

    expect(unsubscribe).toHaveBeenCalledTimes(1)
  })

  it('saveLocal persists, queues, refreshes count, and syncs when online', async () => {
    mockIsOnline.mockReturnValue(true)
    const { result } = renderHookWithProviders(() => useOfflineAnnotations())

    await act(async () => {
      await result.current.saveLocal('ds-1', 'ep-1', 'ann-1', { foo: 'bar' })
    })

    expect(mockSaveAnnotationLocal).toHaveBeenCalledWith(
      'ds-1',
      'ep-1',
      'ann-1',
      { foo: 'bar' },
      'pending',
    )
    expect(mockAddToSyncQueue).toHaveBeenCalledWith('update', 'ds-1', 'ep-1', 'ann-1', {
      foo: 'bar',
    })
    expect(mockSyncManager.process).toHaveBeenCalled()
  })

  it('saveLocal does not trigger immediate sync when offline', async () => {
    mockIsOnline.mockReturnValue(false)
    const { result } = renderHookWithProviders(() => useOfflineAnnotations())

    mockSyncManager.process.mockClear()

    await act(async () => {
      await result.current.saveLocal('ds-1', 'ep-1', 'ann-1', { foo: 'bar' })
    })

    expect(mockSyncManager.process).not.toHaveBeenCalled()
  })

  it('deleteLocal queues a delete when annotation exists', async () => {
    mockGetAnnotationLocal.mockResolvedValueOnce({
      id: 'ann-1',
      datasetId: 'ds-1',
      episodeId: 'ep-1',
      data: {},
      syncStatus: 'pending',
      localUpdatedAt: 'now',
    })
    const { result } = renderHookWithProviders(() => useOfflineAnnotations())

    await act(async () => {
      await result.current.deleteLocal('ann-1')
    })

    expect(mockAddToSyncQueue).toHaveBeenCalledWith('delete', 'ds-1', 'ep-1', 'ann-1', null)
    expect(mockDeleteAnnotationLocal).toHaveBeenCalledWith('ann-1')
  })

  it('deleteLocal skips queue when annotation does not exist', async () => {
    mockGetAnnotationLocal.mockResolvedValueOnce(undefined)
    const { result } = renderHookWithProviders(() => useOfflineAnnotations())

    await act(async () => {
      await result.current.deleteLocal('missing')
    })

    expect(mockAddToSyncQueue).not.toHaveBeenCalled()
    expect(mockDeleteAnnotationLocal).toHaveBeenCalledWith('missing')
  })

  it('getLocal returns mapped annotation when present', async () => {
    mockGetAnnotationLocal.mockResolvedValueOnce({
      id: 'ann-1',
      datasetId: 'ds-1',
      episodeId: 'ep-1',
      data: { x: 1 },
      syncStatus: 'synced',
      localUpdatedAt: 't',
      extraField: 'ignored',
    })
    const { result } = renderHookWithProviders(() => useOfflineAnnotations())

    const value = await result.current.getLocal('ann-1')

    expect(value).toEqual({
      id: 'ann-1',
      datasetId: 'ds-1',
      episodeId: 'ep-1',
      data: { x: 1 },
      syncStatus: 'synced',
      localUpdatedAt: 't',
    })
  })

  it('getLocal returns undefined when annotation is missing', async () => {
    mockGetAnnotationLocal.mockResolvedValueOnce(undefined)
    const { result } = renderHookWithProviders(() => useOfflineAnnotations())

    const value = await result.current.getLocal('missing')

    expect(value).toBeUndefined()
  })

  it('getPending maps results from storage', async () => {
    mockGetAnnotationsBySyncStatus.mockResolvedValue([
      {
        id: 'a',
        datasetId: 'd',
        episodeId: 'e',
        data: 1,
        syncStatus: 'pending',
        localUpdatedAt: 't',
      },
    ])
    const { result } = renderHookWithProviders(() => useOfflineAnnotations())

    const pending = await result.current.getPending()

    expect(pending).toHaveLength(1)
    expect(pending[0].id).toBe('a')
  })

  it('sync toggles isSyncing and stores last result', async () => {
    const syncResult = { success: true, syncedCount: 2, failedCount: 0 }
    mockSyncManager.process.mockResolvedValueOnce(syncResult)
    const { result } = renderHookWithProviders(() => useOfflineAnnotations())

    await act(async () => {
      const r = await result.current.sync()
      expect(r).toEqual(syncResult)
    })

    expect(result.current.isSyncing).toBe(false)
    expect(result.current.lastSyncResult).toEqual(syncResult)
  })

  it('startSync and stopSync delegate to syncQueueManager', () => {
    const { result } = renderHookWithProviders(() => useOfflineAnnotations())

    act(() => {
      result.current.startSync()
    })
    expect(mockSyncManager.start).toHaveBeenCalledTimes(1)

    act(() => {
      result.current.stopSync()
    })
    expect(mockSyncManager.stop).toHaveBeenCalledTimes(1)
  })

  it('listener invocation refreshes pending count and lastSyncResult', async () => {
    let captured: ((r: unknown) => void) | undefined
    mockSyncManager.addListener.mockImplementation((fn: (r: unknown) => void) => {
      captured = fn
      return () => {}
    })

    const { result } = renderHookWithProviders(() => useOfflineAnnotations())

    await waitFor(() => expect(captured).toBeDefined())

    mockGetAnnotationsBySyncStatus.mockResolvedValueOnce([{ id: 'p' }])
    const syncResult = { success: true, syncedCount: 1, failedCount: 0 }

    await act(async () => {
      captured?.(syncResult)
    })

    await waitFor(() => {
      expect(result.current.lastSyncResult).toEqual(syncResult)
      expect(result.current.pendingCount).toBe(1)
    })
  })
})
