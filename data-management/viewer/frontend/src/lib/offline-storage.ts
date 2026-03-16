/**
 * IndexedDB storage service for offline annotation support.
 *
 * Provides local persistence of annotations and pending sync queue.
 */

import { type DBSchema, type IDBPDatabase, openDB } from 'idb'

/** Schema for the annotation offline database */
interface AnnotationDBSchema extends DBSchema {
  annotations: {
    key: string
    value: {
      id: string
      datasetId: string
      episodeId: string
      data: unknown
      localUpdatedAt: string
      serverUpdatedAt?: string
      syncStatus: 'synced' | 'pending' | 'conflict'
    }
    indexes: {
      'by-dataset': string
      'by-sync-status': string
    }
  }
  syncQueue: {
    key: string
    value: {
      id: string
      type: 'create' | 'update' | 'delete'
      datasetId: string
      episodeId: string
      annotationId: string
      payload: unknown
      createdAt: string
      retryCount: number
      lastError?: string
    }
    indexes: {
      'by-created': string
    }
  }
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
const DB_VERSION = 1

let dbInstance: IDBPDatabase<AnnotationDBSchema> | null = null

/**
 * Initialize and get the database instance.
 */
export async function getDB(): Promise<IDBPDatabase<AnnotationDBSchema>> {
  if (dbInstance) {
    return dbInstance
  }

  dbInstance = await openDB<AnnotationDBSchema>(DB_NAME, DB_VERSION, {
    upgrade(db: IDBPDatabase<AnnotationDBSchema>) {
      // Annotations store
      if (!db.objectStoreNames.contains('annotations')) {
        const annotationStore = db.createObjectStore('annotations', {
          keyPath: 'id',
        })
        annotationStore.createIndex('by-dataset', 'datasetId')
        annotationStore.createIndex('by-sync-status', 'syncStatus')
      }

      // Sync queue store
      if (!db.objectStoreNames.contains('syncQueue')) {
        const syncStore = db.createObjectStore('syncQueue', {
          keyPath: 'id',
        })
        syncStore.createIndex('by-created', 'createdAt')
      }

      // Metadata store
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

// Annotation operations

/**
 * Save an annotation locally.
 */
export async function saveAnnotationLocal(
  datasetId: string,
  episodeId: string,
  annotationId: string,
  data: unknown,
  syncStatus: 'synced' | 'pending' = 'pending',
): Promise<void> {
  const db = await getDB()
  await db.put('annotations', {
    id: annotationId,
    datasetId,
    episodeId,
    data,
    localUpdatedAt: new Date().toISOString(),
    syncStatus,
  })
}

/**
 * Get an annotation by ID.
 */
export async function getAnnotationLocal(
  annotationId: string,
): Promise<AnnotationDBSchema['annotations']['value'] | undefined> {
  const db = await getDB()
  return db.get('annotations', annotationId)
}

/**
 * Get all annotations for a dataset.
 */
export async function getAnnotationsByDataset(
  datasetId: string,
): Promise<AnnotationDBSchema['annotations']['value'][]> {
  const db = await getDB()
  return db.getAllFromIndex('annotations', 'by-dataset', datasetId)
}

/**
 * Get annotations by sync status.
 */
export async function getAnnotationsBySyncStatus(
  status: 'synced' | 'pending' | 'conflict',
): Promise<AnnotationDBSchema['annotations']['value'][]> {
  const db = await getDB()
  return db.getAllFromIndex('annotations', 'by-sync-status', status)
}

/**
 * Update annotation sync status.
 */
export async function updateAnnotationSyncStatus(
  annotationId: string,
  syncStatus: 'synced' | 'pending' | 'conflict',
  serverUpdatedAt?: string,
): Promise<void> {
  const db = await getDB()
  const annotation = await db.get('annotations', annotationId)
  if (annotation) {
    await db.put('annotations', {
      ...annotation,
      syncStatus,
      serverUpdatedAt,
    })
  }
}

/**
 * Delete an annotation locally.
 */
export async function deleteAnnotationLocal(annotationId: string): Promise<void> {
  const db = await getDB()
  await db.delete('annotations', annotationId)
}

// Sync queue operations

/**
 * Add an item to the sync queue.
 */
export async function addToSyncQueue(
  type: 'create' | 'update' | 'delete',
  datasetId: string,
  episodeId: string,
  annotationId: string,
  payload: unknown,
): Promise<string> {
  const db = await getDB()
  const id = `sync-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`

  await db.put('syncQueue', {
    id,
    type,
    datasetId,
    episodeId,
    annotationId,
    payload,
    createdAt: new Date().toISOString(),
    retryCount: 0,
  })

  return id
}

/**
 * Get all pending sync items.
 */
export async function getPendingSyncItems(): Promise<AnnotationDBSchema['syncQueue']['value'][]> {
  const db = await getDB()
  return db.getAllFromIndex('syncQueue', 'by-created')
}

/**
 * Remove a sync item after successful sync.
 */
export async function removeSyncItem(id: string): Promise<void> {
  const db = await getDB()
  await db.delete('syncQueue', id)
}

/**
 * Update sync item retry count and error.
 */
export async function updateSyncItemRetry(id: string, error: string): Promise<void> {
  const db = await getDB()
  const item = await db.get('syncQueue', id)
  if (item) {
    await db.put('syncQueue', {
      ...item,
      retryCount: item.retryCount + 1,
      lastError: error,
    })
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

/**
 * Clear all local data.
 */
export async function clearAllLocalData(): Promise<void> {
  const db = await getDB()
  await db.clear('annotations')
  await db.clear('syncQueue')
  await db.clear('metadata')
}
