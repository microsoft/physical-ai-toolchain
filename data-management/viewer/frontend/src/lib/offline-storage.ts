/**
 * IndexedDB storage service for local draft recovery.
 *
 * Stores principal-scoped draft envelopes in one metadata store.
 */

import { type DBSchema, type IDBPDatabase, openDB } from 'idb'

/** Schema for principal-scoped draft metadata. */
interface AnnotationDBSchema extends DBSchema {
  metadata: {
    key: string
    value: {
      key: string
      value: unknown
      updatedAt: string
    }
  }
}

const DB_NAME = 'robotic-training-annotations'
const DB_VERSION = 2

let dbInstance: IDBPDatabase<AnnotationDBSchema> | null = null

/**
 * Initialize and get the database instance.
 */
export async function getDB(): Promise<IDBPDatabase<AnnotationDBSchema>> {
  if (dbInstance) {
    return dbInstance
  }

  dbInstance = await openDB<AnnotationDBSchema>(DB_NAME, DB_VERSION, {
    upgrade(db) {
      const legacyDatabase = db as unknown as IDBDatabase
      if (legacyDatabase.objectStoreNames.contains('annotations')) {
        legacyDatabase.deleteObjectStore('annotations')
      }
      if (legacyDatabase.objectStoreNames.contains('syncQueue')) {
        legacyDatabase.deleteObjectStore('syncQueue')
      }
      if (!db.objectStoreNames.contains('metadata')) {
        db.createObjectStore('metadata', { keyPath: 'key' })
      }
    },
  })

  return dbInstance
}

/**
 * Close the database connection.
 */
export async function closeDB(): Promise<void> {
  if (dbInstance) {
    dbInstance.close()
    dbInstance = null
  }
}

// Metadata operations

/**
 * Save metadata value.
 */
export async function setMetadata(key: string, value: unknown): Promise<void> {
  const db = await getDB()
  await db.put('metadata', {
    key,
    value,
    updatedAt: new Date().toISOString(),
  })
}

/**
 * Get metadata value.
 */
export async function getMetadata<T = unknown>(key: string): Promise<T | undefined> {
  const db = await getDB()
  const item = await db.get('metadata', key)
  return item?.value as T | undefined
}

/**
 * Delete metadata value.
 */
export async function deleteMetadata(key: string): Promise<void> {
  const db = await getDB()
  await db.delete('metadata', key)
}

export async function deleteMetadataByPrefixes(prefixes: string[]): Promise<void> {
  const db = await getDB()
  const transaction = db.transaction('metadata', 'readwrite')
  const keys = await transaction.store.getAllKeys()
  await Promise.all(
    keys
      .filter((key) => prefixes.some((prefix) => String(key).startsWith(prefix)))
      .map((key) => transaction.store.delete(key)),
  )
  await transaction.done
}

/**
 * Clear all local data.
 */
export async function clearAllLocalData(): Promise<void> {
  const db = await getDB()
  await db.clear('metadata')
}
