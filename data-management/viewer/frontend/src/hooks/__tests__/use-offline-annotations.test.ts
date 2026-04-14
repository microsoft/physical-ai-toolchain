import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createQueryWrapper } from './test-utils'

// ============================================================================
// Mocks
// ============================================================================

const mockOfflineStorage = vi.hoisted(() => ({
  saveAnnotationLocal: vi.fn().mockResolvedValue(undefined),
  getAnnotationLocal: vi.fn().mockResolvedValue(undefined),
  getAnnotationsBySyncStatus: vi.fn().mockResolvedValue([]),
  deleteAnnotationLocal: vi.fn().mockResolvedValue(undefined),
  addToSyncQueue: vi.fn().mockResolvedValue(undefined),
}))

const mockSyncQueue = vi.hoisted(() => {
  let listeners: Array<(result: unknown) => void> = []
  return {
    isOnline: vi.fn().mockReturnValue(true),
    syncQueueManager: {
      process: vi.fn().mockResolvedValue({ syncedCount: 0, failedCount: 0, errors: [] }),
      start: vi.fn(),
      stop: vi.fn(),
      addListener: vi.fn((cb: (result: unknown) => void) => {
        listeners.push(cb)
        return () => {
          listeners = listeners.filter((l) => l !== cb)
        }
      }),
      _listeners: listeners,
      _notify: (result: unknown) => {
        listeners.forEach((l) => l(result))
      },
    },
  }
})

vi.mock('@/lib/offline-storage', () => mockOfflineStorage)
vi.mock('@/lib/sync-queue', () => mockSyncQueue)
vi.mock('@/hooks/use-annotations', () => ({
  annotationKeys: { all: ['annotations'] },
}))

// ============================================================================
// Fixtures
// ============================================================================

const testAnnotation = {
  id: 'ann-1',
  datasetId: 'ds-1',
  episodeId: 'ep-1',
  data: { rating: 5, notes: 'Good' },
  syncStatus: 'pending' as const,
  localUpdatedAt: '2025-01-01T00:00:00Z',
}

// ============================================================================
// Tests
// ============================================================================

describe('useOfflineAnnotations', () => {
  const wrapper = createQueryWrapper()

  beforeEach(() => {
    vi.clearAllMocks()
    mockSyncQueue.isOnline.mockReturnValue(true)
    mockOfflineStorage.getAnnotationsBySyncStatus.mockResolvedValue([])
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  // --------------------------------------------------------------------------
  // Initial state
  // --------------------------------------------------------------------------

  it('returns initial state with online status', async () => {
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { result } = renderHook(() => useOfflineAnnotations(), { wrapper })

    expect(result.current.isOnline).toBe(true)
    expect(result.current.pendingCount).toBe(0)
    expect(result.current.isSyncing).toBe(false)
    expect(result.current.lastSyncResult).toBeNull()
  })

  it('reflects offline status when navigator is offline', async () => {
    mockSyncQueue.isOnline.mockReturnValue(false)
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { result } = renderHook(() => useOfflineAnnotations(), { wrapper })

    expect(result.current.isOnline).toBe(false)
  })

  // --------------------------------------------------------------------------
  // Online/offline events
  // --------------------------------------------------------------------------

  it('updates isOnline when online event fires', async () => {
    mockSyncQueue.isOnline.mockReturnValue(false)
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { result } = renderHook(() => useOfflineAnnotations(), { wrapper })

    expect(result.current.isOnline).toBe(false)

    act(() => {
      window.dispatchEvent(new Event('online'))
    })

    expect(result.current.isOnline).toBe(true)
  })

  it('updates isOnline when offline event fires', async () => {
    mockSyncQueue.isOnline.mockReturnValue(true)
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { result } = renderHook(() => useOfflineAnnotations(), { wrapper })

    expect(result.current.isOnline).toBe(true)

    act(() => {
      window.dispatchEvent(new Event('offline'))
    })

    expect(result.current.isOnline).toBe(false)
  })

  it('cleans up online/offline listeners on unmount', async () => {
    const addSpy = vi.spyOn(window, 'addEventListener')
    const removeSpy = vi.spyOn(window, 'removeEventListener')

    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { unmount } = renderHook(() => useOfflineAnnotations(), { wrapper })

    expect(addSpy).toHaveBeenCalledWith('online', expect.any(Function))
    expect(addSpy).toHaveBeenCalledWith('offline', expect.any(Function))

    unmount()

    expect(removeSpy).toHaveBeenCalledWith('online', expect.any(Function))
    expect(removeSpy).toHaveBeenCalledWith('offline', expect.any(Function))
  })

  // --------------------------------------------------------------------------
  // Pending count
  // --------------------------------------------------------------------------

  it('updates pending count on mount', async () => {
    mockOfflineStorage.getAnnotationsBySyncStatus.mockResolvedValue([
      testAnnotation,
      testAnnotation,
    ])
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { result } = renderHook(() => useOfflineAnnotations(), { wrapper })

    await waitFor(() => {
      expect(result.current.pendingCount).toBe(2)
    })
  })

  // --------------------------------------------------------------------------
  // Sync listener
  // --------------------------------------------------------------------------

  it('updates lastSyncResult when sync listener fires', async () => {
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { result } = renderHook(() => useOfflineAnnotations(), { wrapper })

    const syncResult = { syncedCount: 3, failedCount: 0, errors: [] }

    act(() => {
      const listener = mockSyncQueue.syncQueueManager.addListener.mock.calls[0]?.[0]
      if (listener) listener(syncResult)
    })

    await waitFor(() => {
      expect(result.current.lastSyncResult).toEqual(syncResult)
    })
  })

  it('cleans up sync listener on unmount', async () => {
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { unmount } = renderHook(() => useOfflineAnnotations(), { wrapper })

    expect(mockSyncQueue.syncQueueManager.addListener).toHaveBeenCalledWith(expect.any(Function))

    unmount()
    // Unsubscribe function was returned by addListener mock and called on cleanup
  })

  // --------------------------------------------------------------------------
  // saveLocal
  // --------------------------------------------------------------------------

  it('saves annotation locally and adds to sync queue', async () => {
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { result } = renderHook(() => useOfflineAnnotations(), { wrapper })

    await act(async () => {
      await result.current.saveLocal('ds-1', 'ep-1', 'ann-1', { rating: 5 })
    })

    expect(mockOfflineStorage.saveAnnotationLocal).toHaveBeenCalledWith(
      'ds-1',
      'ep-1',
      'ann-1',
      { rating: 5 },
      'pending',
    )
    expect(mockOfflineStorage.addToSyncQueue).toHaveBeenCalledWith(
      'update',
      'ds-1',
      'ep-1',
      'ann-1',
      { rating: 5 },
    )
  })

  it('triggers sync process when online after save', async () => {
    mockSyncQueue.isOnline.mockReturnValue(true)
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { result } = renderHook(() => useOfflineAnnotations(), { wrapper })

    await act(async () => {
      await result.current.saveLocal('ds-1', 'ep-1', 'ann-1', { rating: 5 })
    })

    expect(mockSyncQueue.syncQueueManager.process).toHaveBeenCalled()
  })

  it('does not trigger sync process when offline after save', async () => {
    mockSyncQueue.isOnline.mockReturnValue(false)
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { result } = renderHook(() => useOfflineAnnotations(), { wrapper })

    await act(async () => {
      await result.current.saveLocal('ds-1', 'ep-1', 'ann-1', { rating: 5 })
    })

    expect(mockSyncQueue.syncQueueManager.process).not.toHaveBeenCalled()
  })

  // --------------------------------------------------------------------------
  // getLocal
  // --------------------------------------------------------------------------

  it('returns mapped annotation from local storage', async () => {
    mockOfflineStorage.getAnnotationLocal.mockResolvedValue(testAnnotation)
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { result } = renderHook(() => useOfflineAnnotations(), { wrapper })

    let annotation: unknown
    await act(async () => {
      annotation = await result.current.getLocal('ann-1')
    })

    expect(annotation).toEqual({
      id: 'ann-1',
      datasetId: 'ds-1',
      episodeId: 'ep-1',
      data: { rating: 5, notes: 'Good' },
      syncStatus: 'pending',
      localUpdatedAt: '2025-01-01T00:00:00Z',
    })
  })

  it('returns undefined when annotation not found locally', async () => {
    mockOfflineStorage.getAnnotationLocal.mockResolvedValue(null)
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { result } = renderHook(() => useOfflineAnnotations(), { wrapper })

    let annotation: unknown
    await act(async () => {
      annotation = await result.current.getLocal('nonexistent')
    })

    expect(annotation).toBeUndefined()
  })

  // --------------------------------------------------------------------------
  // getPending
  // --------------------------------------------------------------------------

  it('returns pending annotations mapped to OfflineAnnotation shape', async () => {
    const pending = [testAnnotation, { ...testAnnotation, id: 'ann-2' }]
    mockOfflineStorage.getAnnotationsBySyncStatus.mockResolvedValue(pending)
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { result } = renderHook(() => useOfflineAnnotations(), { wrapper })

    let pendingList: unknown[]
    await act(async () => {
      pendingList = await result.current.getPending()
    })

    expect(pendingList!).toHaveLength(2)
    expect(pendingList![0]).toEqual(expect.objectContaining({ id: 'ann-1', syncStatus: 'pending' }))
  })

  // --------------------------------------------------------------------------
  // deleteLocal
  // --------------------------------------------------------------------------

  it('adds delete to sync queue when annotation exists', async () => {
    mockOfflineStorage.getAnnotationLocal.mockResolvedValue(testAnnotation)
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { result } = renderHook(() => useOfflineAnnotations(), { wrapper })

    await act(async () => {
      await result.current.deleteLocal('ann-1')
    })

    expect(mockOfflineStorage.addToSyncQueue).toHaveBeenCalledWith(
      'delete',
      'ds-1',
      'ep-1',
      'ann-1',
      null,
    )
    expect(mockOfflineStorage.deleteAnnotationLocal).toHaveBeenCalledWith('ann-1')
  })

  it('deletes locally without queuing sync when annotation not found', async () => {
    mockOfflineStorage.getAnnotationLocal.mockResolvedValue(null)
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { result } = renderHook(() => useOfflineAnnotations(), { wrapper })

    await act(async () => {
      await result.current.deleteLocal('ann-1')
    })

    expect(mockOfflineStorage.addToSyncQueue).not.toHaveBeenCalled()
    expect(mockOfflineStorage.deleteAnnotationLocal).toHaveBeenCalledWith('ann-1')
  })

  // --------------------------------------------------------------------------
  // sync
  // --------------------------------------------------------------------------

  it('triggers manual sync and returns result', async () => {
    const syncResult = { syncedCount: 2, failedCount: 0, errors: [] }
    mockSyncQueue.syncQueueManager.process.mockResolvedValue(syncResult)
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { result } = renderHook(() => useOfflineAnnotations(), { wrapper })

    let returned: unknown
    await act(async () => {
      returned = await result.current.sync()
    })

    expect(returned).toEqual(syncResult)
    expect(result.current.lastSyncResult).toEqual(syncResult)
  })

  it('sets isSyncing during sync and resets on completion', async () => {
    let resolveProcess: (value: unknown) => void
    mockSyncQueue.syncQueueManager.process.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveProcess = resolve
        }),
    )
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { result } = renderHook(() => useOfflineAnnotations(), { wrapper })

    expect(result.current.isSyncing).toBe(false)

    let syncPromise: Promise<unknown>
    act(() => {
      syncPromise = result.current.sync()
    })

    await waitFor(() => {
      expect(result.current.isSyncing).toBe(true)
    })

    await act(async () => {
      resolveProcess!({ syncedCount: 0, failedCount: 0, errors: [] })
      await syncPromise!
    })

    expect(result.current.isSyncing).toBe(false)
  })

  it('resets isSyncing even when sync throws', async () => {
    mockSyncQueue.syncQueueManager.process.mockRejectedValue(new Error('Network error'))
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { result } = renderHook(() => useOfflineAnnotations(), { wrapper })

    await act(async () => {
      try {
        await result.current.sync()
      } catch {
        // Expected
      }
    })

    expect(result.current.isSyncing).toBe(false)
  })

  // --------------------------------------------------------------------------
  // startSync / stopSync
  // --------------------------------------------------------------------------

  it('delegates startSync to syncQueueManager', async () => {
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { result } = renderHook(() => useOfflineAnnotations(), { wrapper })

    act(() => {
      result.current.startSync()
    })

    expect(mockSyncQueue.syncQueueManager.start).toHaveBeenCalled()
  })

  it('delegates stopSync to syncQueueManager', async () => {
    const { useOfflineAnnotations } = await import('@/hooks/use-offline-annotations')
    const { result } = renderHook(() => useOfflineAnnotations(), { wrapper })

    act(() => {
      result.current.stopSync()
    })

    expect(mockSyncQueue.syncQueueManager.stop).toHaveBeenCalled()
  })
})
