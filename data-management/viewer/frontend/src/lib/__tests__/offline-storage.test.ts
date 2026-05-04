import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  addToSyncQueue,
  clearAllLocalData,
  closeDB,
  deleteAnnotationLocal,
  deleteMetadata,
  getAnnotationLocal,
  getAnnotationsByDataset,
  getAnnotationsBySyncStatus,
  getDB,
  getMetadata,
  getPendingSyncItems,
  removeSyncItem,
  saveAnnotationLocal,
  setMetadata,
  updateAnnotationSyncStatus,
  updateSyncItemRetry,
} from '@/lib/offline-storage'

const DB_NAME = 'robotic-training-annotations'

async function resetDB() {
  await closeDB()
  await new Promise<void>((resolve) => {
    const req = indexedDB.deleteDatabase(DB_NAME)
    req.onsuccess = () => resolve()
    req.onerror = () => resolve()
    req.onblocked = () => resolve()
  })
}

describe('offline-storage', () => {
  beforeEach(async () => {
    await resetDB()
  })

  afterEach(async () => {
    await resetDB()
  })

  it('getDB returns a singleton instance with required object stores', async () => {
    const db1 = await getDB()
    const db2 = await getDB()
    expect(db1).toBe(db2)
    expect(Array.from(db1.objectStoreNames).sort()).toEqual(
      ['annotations', 'metadata', 'syncQueue'].sort(),
    )
  })

  describe('annotations', () => {
    it('saveAnnotationLocal defaults syncStatus to pending', async () => {
      await saveAnnotationLocal('ds1', 'ep1', 'a1', { v: 1 })
      const got = await getAnnotationLocal('a1')
      expect(got).toBeDefined()
      expect(got?.syncStatus).toBe('pending')
      expect(got?.datasetId).toBe('ds1')
      expect(got?.episodeId).toBe('ep1')
      expect(got?.data).toEqual({ v: 1 })
      expect(typeof got?.localUpdatedAt).toBe('string')
    })

    it('saveAnnotationLocal accepts explicit synced status', async () => {
      await saveAnnotationLocal('ds1', 'ep1', 'a1', { v: 1 }, 'synced')
      const got = await getAnnotationLocal('a1')
      expect(got?.syncStatus).toBe('synced')
    })

    it('getAnnotationLocal returns undefined for missing id', async () => {
      const got = await getAnnotationLocal('missing')
      expect(got).toBeUndefined()
    })

    it('getAnnotationsByDataset returns only annotations for the given dataset', async () => {
      await saveAnnotationLocal('ds1', 'ep1', 'a1', { v: 1 })
      await saveAnnotationLocal('ds1', 'ep2', 'a2', { v: 2 })
      await saveAnnotationLocal('ds2', 'ep1', 'a3', { v: 3 })
      const items = await getAnnotationsByDataset('ds1')
      expect(items.map((i) => i.id).sort()).toEqual(['a1', 'a2'])
    })

    it('getAnnotationsBySyncStatus filters by status', async () => {
      await saveAnnotationLocal('ds1', 'ep1', 'a1', {}, 'pending')
      await saveAnnotationLocal('ds1', 'ep2', 'a2', {}, 'synced')
      const pending = await getAnnotationsBySyncStatus('pending')
      const synced = await getAnnotationsBySyncStatus('synced')
      expect(pending.map((i) => i.id)).toEqual(['a1'])
      expect(synced.map((i) => i.id)).toEqual(['a2'])
    })

    it('updateAnnotationSyncStatus updates status and serverUpdatedAt', async () => {
      await saveAnnotationLocal('ds1', 'ep1', 'a1', {}, 'pending')
      await updateAnnotationSyncStatus('a1', 'synced', '2024-01-01T00:00:00.000Z')
      const got = await getAnnotationLocal('a1')
      expect(got?.syncStatus).toBe('synced')
      expect(got?.serverUpdatedAt).toBe('2024-01-01T00:00:00.000Z')
    })

    it('updateAnnotationSyncStatus omits serverUpdatedAt when not provided', async () => {
      await saveAnnotationLocal('ds1', 'ep1', 'a1', {}, 'pending')
      await updateAnnotationSyncStatus('a1', 'conflict')
      const got = await getAnnotationLocal('a1')
      expect(got?.syncStatus).toBe('conflict')
      expect(got?.serverUpdatedAt).toBeUndefined()
    })

    it('updateAnnotationSyncStatus is a no-op for missing id', async () => {
      await updateAnnotationSyncStatus('missing', 'synced')
      const got = await getAnnotationLocal('missing')
      expect(got).toBeUndefined()
    })

    it('deleteAnnotationLocal removes the annotation', async () => {
      await saveAnnotationLocal('ds1', 'ep1', 'a1', {})
      await deleteAnnotationLocal('a1')
      expect(await getAnnotationLocal('a1')).toBeUndefined()
    })
  })

  describe('sync queue', () => {
    it('addToSyncQueue returns id with sync- prefix and persists fields', async () => {
      const id = await addToSyncQueue('create', 'ds1', 'ep1', 'a1', { foo: 'bar' })
      expect(id).toMatch(/^sync-\d+-[a-z0-9]+$/)
      const items = await getPendingSyncItems()
      expect(items).toHaveLength(1)
      expect(items[0]).toMatchObject({
        id,
        type: 'create',
        datasetId: 'ds1',
        episodeId: 'ep1',
        annotationId: 'a1',
        payload: { foo: 'bar' },
        retryCount: 0,
      })
      expect(typeof items[0].createdAt).toBe('string')
    })

    it('getPendingSyncItems orders by createdAt index', async () => {
      const id1 = await addToSyncQueue('create', 'ds1', 'ep1', 'a1', {})
      await new Promise((r) => setTimeout(r, 5))
      const id2 = await addToSyncQueue('update', 'ds1', 'ep1', 'a2', {})
      const items = await getPendingSyncItems()
      expect(items.map((i) => i.id)).toEqual([id1, id2])
    })

    it('removeSyncItem deletes the queue item', async () => {
      const id = await addToSyncQueue('delete', 'ds1', 'ep1', 'a1', {})
      await removeSyncItem(id)
      const items = await getPendingSyncItems()
      expect(items).toHaveLength(0)
    })

    it('updateSyncItemRetry increments retryCount and sets lastError', async () => {
      const id = await addToSyncQueue('create', 'ds1', 'ep1', 'a1', {})
      await updateSyncItemRetry(id, 'boom')
      await updateSyncItemRetry(id, 'boom2')
      const items = await getPendingSyncItems()
      expect(items[0].retryCount).toBe(2)
      expect(items[0].lastError).toBe('boom2')
    })

    it('updateSyncItemRetry is a no-op for missing id', async () => {
      await updateSyncItemRetry('missing', 'err')
      const items = await getPendingSyncItems()
      expect(items).toHaveLength(0)
    })
  })

  describe('metadata', () => {
    it('setMetadata + getMetadata round-trips arbitrary values', async () => {
      await setMetadata('k', { hello: 'world' })
      const got = await getMetadata<{ hello: string }>('k')
      expect(got).toEqual({ hello: 'world' })
    })

    it('getMetadata returns undefined for missing key', async () => {
      const got = await getMetadata('missing')
      expect(got).toBeUndefined()
    })

    it('deleteMetadata removes the key', async () => {
      await setMetadata('k', 1)
      await deleteMetadata('k')
      expect(await getMetadata('k')).toBeUndefined()
    })
  })

  it('clearAllLocalData empties annotations, syncQueue, and metadata', async () => {
    await saveAnnotationLocal('ds1', 'ep1', 'a1', {})
    await addToSyncQueue('create', 'ds1', 'ep1', 'a1', {})
    await setMetadata('k', 'v')
    await clearAllLocalData()
    expect(await getAnnotationsByDataset('ds1')).toHaveLength(0)
    expect(await getPendingSyncItems()).toHaveLength(0)
    expect(await getMetadata('k')).toBeUndefined()
  })

  it('closeDB resets the singleton so subsequent getDB returns a fresh instance', async () => {
    const db1 = await getDB()
    await closeDB()
    const db2 = await getDB()
    expect(db1).not.toBe(db2)
  })
})
