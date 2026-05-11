import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  clearPersistedEditDraftsForTests,
  loadPersistedEditDraft,
  persistEditDraft,
} from '../edit-draft-storage'
import { closeDB } from '../offline-storage'

const sampleOperations = {
  frameRemovals: [{ frameIndex: 5 }],
  cropRegion: { x: 0, y: 0, width: 64, height: 64 },
  resizeDimensions: { width: 32, height: 32 },
  subTasks: [],
} as const

async function resetDB(): Promise<void> {
  await closeDB()
  await new Promise<void>((resolve, reject) => {
    const request = indexedDB.deleteDatabase('robotic-training-annotations')
    request.onsuccess = () => resolve()
    request.onerror = () => reject(request.error)
    request.onblocked = () => resolve()
  })
}

describe('edit-draft-storage (IndexedDB path)', () => {
  beforeEach(async () => {
    await resetDB()
    await clearPersistedEditDraftsForTests()
  })

  afterEach(async () => {
    vi.restoreAllMocks()
    await resetDB()
  })

  it('returns undefined when no draft is persisted', async () => {
    const result = await loadPersistedEditDraft('ds-1', 0)
    expect(result).toBeUndefined()
  })

  it('round-trips operations through IndexedDB', async () => {
    await persistEditDraft('ds-1', 7, sampleOperations as never)
    const loaded = await loadPersistedEditDraft('ds-1', 7)
    expect(loaded).toEqual(sampleOperations)
  })

  it('keys are scoped per dataset and episode', async () => {
    await persistEditDraft('ds-1', 0, sampleOperations as never)
    expect(await loadPersistedEditDraft('ds-1', 1)).toBeUndefined()
    expect(await loadPersistedEditDraft('ds-2', 0)).toBeUndefined()
  })

  it('persistEditDraft(null) deletes a previously stored draft', async () => {
    await persistEditDraft('ds-1', 0, sampleOperations as never)
    await persistEditDraft('ds-1', 0, null)
    expect(await loadPersistedEditDraft('ds-1', 0)).toBeUndefined()
  })

  it('persistEditDraft(null) is a no-op when no draft exists', async () => {
    await expect(persistEditDraft('ds-1', 0, null)).resolves.toBeUndefined()
    expect(await loadPersistedEditDraft('ds-1', 0)).toBeUndefined()
  })
})

describe('edit-draft-storage (in-memory fallback)', () => {
  beforeEach(async () => {
    await clearPersistedEditDraftsForTests()
    vi.stubGlobal('indexedDB', undefined)
  })

  afterEach(async () => {
    vi.unstubAllGlobals()
    await clearPersistedEditDraftsForTests()
  })

  it('returns undefined when no draft is in the fallback map', async () => {
    expect(await loadPersistedEditDraft('ds-1', 0)).toBeUndefined()
  })

  it('round-trips operations through the fallback map', async () => {
    await persistEditDraft('ds-1', 0, sampleOperations as never)
    expect(await loadPersistedEditDraft('ds-1', 0)).toEqual(sampleOperations)
  })

  it('persistEditDraft(null) clears an entry in the fallback map', async () => {
    await persistEditDraft('ds-1', 0, sampleOperations as never)
    await persistEditDraft('ds-1', 0, null)
    expect(await loadPersistedEditDraft('ds-1', 0)).toBeUndefined()
  })

  it('persistEditDraft(null) is a no-op in the fallback map when nothing stored', async () => {
    await expect(persistEditDraft('ds-1', 0, null)).resolves.toBeUndefined()
  })

  it('clearPersistedEditDraftsForTests empties the fallback map', async () => {
    await persistEditDraft('ds-1', 0, sampleOperations as never)
    await persistEditDraft('ds-2', 1, sampleOperations as never)
    await clearPersistedEditDraftsForTests()
    expect(await loadPersistedEditDraft('ds-1', 0)).toBeUndefined()
    expect(await loadPersistedEditDraft('ds-2', 1)).toBeUndefined()
  })
})
