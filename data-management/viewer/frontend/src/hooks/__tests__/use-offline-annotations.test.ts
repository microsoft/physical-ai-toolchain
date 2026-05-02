import { act, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useOfflineAnnotations } from '@/hooks/use-offline-annotations'
import { renderHookWithQuery } from '@/test/render-hook-with-query'

const mocks = vi.hoisted(() => {
  const listeners: Array<(result: unknown) => void> = []
  return {
    listeners,
    saveAnnotationLocal: vi.fn(async () => undefined),
    addToSyncQueue: vi.fn(async () => undefined),
    deleteAnnotationLocal: vi.fn(async () => undefined),
    getAnnotationLocal: vi.fn(),
    getAnnotationsBySyncStatus: vi.fn(async () => []),
    isOnline: vi.fn(() => true),
    syncQueueManager: {
      addListener: vi.fn((cb: (result: unknown) => void) => {
        listeners.push(cb)
        return () => {
          const idx = listeners.indexOf(cb)
          if (idx >= 0) listeners.splice(idx, 1)
        }
      }),
      process: vi.fn(async () => ({ syncedCount: 0, failedCount: 0, conflictCount: 0 })),
      start: vi.fn(),
      stop: vi.fn(),
    },
  }
})

vi.mock('@/lib/offline-storage', () => ({
  saveAnnotationLocal: mocks.saveAnnotationLocal,
  addToSyncQueue: mocks.addToSyncQueue,
  deleteAnnotationLocal: mocks.deleteAnnotationLocal,
  getAnnotationLocal: mocks.getAnnotationLocal,
  getAnnotationsBySyncStatus: mocks.getAnnotationsBySyncStatus,
}))

vi.mock('@/lib/sync-queue', () => ({
  isOnline: mocks.isOnline,
  syncQueueManager: mocks.syncQueueManager,
}))

describe('useOfflineAnnotations', () => {
  beforeEach(() => {
    mocks.listeners.length = 0
    mocks.saveAnnotationLocal.mockClear()
    mocks.addToSyncQueue.mockClear()
    mocks.deleteAnnotationLocal.mockClear()
    mocks.getAnnotationLocal.mockReset()
    mocks.getAnnotationsBySyncStatus.mockReset().mockResolvedValue([])
    mocks.isOnline.mockReset().mockReturnValue(true)
    mocks.syncQueueManager.addListener.mockClear()
    mocks.syncQueueManager.process
      .mockReset()
      .mockResolvedValue({ syncedCount: 0, failedCount: 0, conflictCount: 0 })
    mocks.syncQueueManager.start.mockClear()
    mocks.syncQueueManager.stop.mockClear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('reflects online state from isOnline()', async () => {
    mocks.isOnline.mockReturnValue(false)
    const { result } = renderHookWithQuery(() => useOfflineAnnotations())
    expect(result.current.isOnline).toBe(false)
    await waitFor(() => expect(mocks.getAnnotationsBySyncStatus).toHaveBeenCalled())
  })

  it('updates online state in response to window events', async () => {
    mocks.isOnline.mockReturnValue(true)
    const { result } = renderHookWithQuery(() => useOfflineAnnotations())
    expect(result.current.isOnline).toBe(true)

    await act(async () => {
      window.dispatchEvent(new Event('offline'))
    })
    expect(result.current.isOnline).toBe(false)

    await act(async () => {
      window.dispatchEvent(new Event('online'))
    })
    expect(result.current.isOnline).toBe(true)
  })

  it('saveLocal persists, queues update, and triggers process when online', async () => {
    mocks.isOnline.mockReturnValue(true)
    const { result } = renderHookWithQuery(() => useOfflineAnnotations())

    await act(async () => {
      await result.current.saveLocal('ds-1', 'ep-2', 'ann-3', { foo: 'bar' })
    })

    expect(mocks.saveAnnotationLocal).toHaveBeenCalledWith(
      'ds-1',
      'ep-2',
      'ann-3',
      { foo: 'bar' },
      'pending',
    )
    expect(mocks.addToSyncQueue).toHaveBeenCalledWith('update', 'ds-1', 'ep-2', 'ann-3', {
      foo: 'bar',
    })
    expect(mocks.syncQueueManager.process).toHaveBeenCalled()
  })

  it('saveLocal does not call process when offline', async () => {
    mocks.isOnline.mockReturnValue(false)
    const { result } = renderHookWithQuery(() => useOfflineAnnotations())

    await act(async () => {
      await result.current.saveLocal('ds-1', 'ep-2', 'ann-3', { foo: 'bar' })
    })

    expect(mocks.syncQueueManager.process).not.toHaveBeenCalled()
  })

  it('getLocal maps storage record to OfflineAnnotation', async () => {
    mocks.getAnnotationLocal.mockResolvedValue({
      id: 'ann-3',
      datasetId: 'ds-1',
      episodeId: 'ep-2',
      data: { value: 1 },
      syncStatus: 'pending',
      localUpdatedAt: '2025-01-01T00:00:00Z',
    })

    const { result } = renderHookWithQuery(() => useOfflineAnnotations())

    const annotation = await result.current.getLocal('ann-3')
    expect(annotation).toEqual({
      id: 'ann-3',
      datasetId: 'ds-1',
      episodeId: 'ep-2',
      data: { value: 1 },
      syncStatus: 'pending',
      localUpdatedAt: '2025-01-01T00:00:00Z',
    })
  })

  it('getLocal returns undefined when storage misses', async () => {
    mocks.getAnnotationLocal.mockResolvedValue(undefined)
    const { result } = renderHookWithQuery(() => useOfflineAnnotations())
    await expect(result.current.getLocal('missing')).resolves.toBeUndefined()
  })

  it('getPending returns mapped pending annotations', async () => {
    mocks.getAnnotationsBySyncStatus.mockResolvedValue([
      {
        id: 'a1',
        datasetId: 'd1',
        episodeId: 'e1',
        data: { x: 1 },
        syncStatus: 'pending',
        localUpdatedAt: 't1',
      },
    ])

    const { result } = renderHookWithQuery(() => useOfflineAnnotations())
    const pending = await result.current.getPending()
    expect(pending).toHaveLength(1)
    expect(pending[0]).toMatchObject({ id: 'a1', syncStatus: 'pending' })
  })

  it('deleteLocal queues delete when annotation exists then removes', async () => {
    mocks.getAnnotationLocal.mockResolvedValue({
      id: 'ann-3',
      datasetId: 'ds-1',
      episodeId: 'ep-2',
      data: { value: 1 },
      syncStatus: 'pending',
      localUpdatedAt: 't',
    })

    const { result } = renderHookWithQuery(() => useOfflineAnnotations())

    await act(async () => {
      await result.current.deleteLocal('ann-3')
    })

    expect(mocks.addToSyncQueue).toHaveBeenCalledWith('delete', 'ds-1', 'ep-2', 'ann-3', null)
    expect(mocks.deleteAnnotationLocal).toHaveBeenCalledWith('ann-3')
  })

  it('deleteLocal skips queue entry when annotation missing', async () => {
    mocks.getAnnotationLocal.mockResolvedValue(undefined)
    const { result } = renderHookWithQuery(() => useOfflineAnnotations())

    await act(async () => {
      await result.current.deleteLocal('missing')
    })

    expect(mocks.addToSyncQueue).not.toHaveBeenCalled()
    expect(mocks.deleteAnnotationLocal).toHaveBeenCalledWith('missing')
  })

  it('sync toggles isSyncing and stores result', async () => {
    const syncResult = { syncedCount: 2, failedCount: 0, conflictCount: 0 }
    mocks.syncQueueManager.process.mockResolvedValue(syncResult)

    const { result } = renderHookWithQuery(() => useOfflineAnnotations())

    let returned: unknown
    await act(async () => {
      returned = await result.current.sync()
    })

    expect(returned).toEqual(syncResult)
    expect(result.current.isSyncing).toBe(false)
    expect(result.current.lastSyncResult).toEqual(syncResult)
  })

  it('startSync and stopSync delegate to syncQueueManager', () => {
    const { result } = renderHookWithQuery(() => useOfflineAnnotations())
    act(() => result.current.startSync())
    expect(mocks.syncQueueManager.start).toHaveBeenCalled()
    act(() => result.current.stopSync())
    expect(mocks.syncQueueManager.stop).toHaveBeenCalled()
  })

  it('listener invalidates annotation queries when items synced', async () => {
    const { result, queryClient } = renderHookWithQuery(() => useOfflineAnnotations())
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    await waitFor(() => expect(mocks.syncQueueManager.addListener).toHaveBeenCalled())
    expect(mocks.listeners.length).toBeGreaterThan(0)

    await act(async () => {
      mocks.listeners[0]({ syncedCount: 1, failedCount: 0, conflictCount: 0 })
    })

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['annotations'] })
    expect(result.current.lastSyncResult).toEqual({
      syncedCount: 1,
      failedCount: 0,
      conflictCount: 0,
    })
  })
})
