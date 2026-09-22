import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  clearAllLocalData,
  closeDB,
  deleteMetadata,
  getDB,
  getMetadata,
  setMetadata,
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
    expect(Array.from(db1.objectStoreNames)).toEqual(['metadata'])
  })

  it('upgrades a version-1 database by deleting obsolete stores', async () => {
    await closeDB()
    await new Promise<void>((resolve, reject) => {
      const request = indexedDB.deleteDatabase(DB_NAME)
      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error)
    })
    await new Promise<void>((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, 1)
      request.onupgradeneeded = () => {
        request.result.createObjectStore('annotations', { keyPath: 'id' })
        request.result.createObjectStore('syncQueue', { keyPath: 'id' })
        request.result.createObjectStore('metadata', { keyPath: 'key' })
      }
      request.onsuccess = () => {
        request.result.close()
        resolve()
      }
      request.onerror = () => reject(request.error)
    })

    const upgraded = await getDB()

    expect(Array.from(upgraded.objectStoreNames)).toEqual(['metadata'])
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

  it('clearAllLocalData empties metadata', async () => {
    await setMetadata('k', 'v')
    await clearAllLocalData()
    expect(await getMetadata('k')).toBeUndefined()
  })

  it('closeDB resets the singleton so subsequent getDB returns a fresh instance', async () => {
    const db1 = await getDB()
    await closeDB()
    const db2 = await getDB()
    expect(db1).not.toBe(db2)
  })
})
