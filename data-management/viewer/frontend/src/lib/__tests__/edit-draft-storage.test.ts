import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  clearPersistedEditDraftsForTests,
  loadPersistedAnnotationDraft,
  loadPersistedDraftEnvelope,
  loadPersistedEditDraft,
  loadPersistedLabelDraft,
  mergeLabelSets,
  mergeThreeWay,
  persistAnnotationDraft,
  persistDraftEnvelope,
  persistEditDraft,
  persistLabelDraft,
} from '../edit-draft-storage'
import { closeDB, getMetadata, setMetadata } from '../offline-storage'

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
    const result = await loadPersistedEditDraft('ds-1', 0, 'principal-one')
    expect(result).toBeUndefined()
  })

  it('round-trips operations through IndexedDB', async () => {
    await persistEditDraft('ds-1', 7, 'principal-one', sampleOperations as never)
    const loaded = await loadPersistedEditDraft('ds-1', 7, 'principal-one')
    expect(loaded?.schemaVersion).toBe(2)
    expect(loaded?.principalScopeId).toBe('principal-one')
    expect(loaded?.resource).toEqual({ kind: 'episode-edit', datasetId: 'ds-1', episodeIndex: 7 })
    expect(loaded?.draft).toEqual(sampleOperations)
  })

  it('keys are scoped per dataset and episode', async () => {
    await persistEditDraft('ds-1', 0, 'principal-one', sampleOperations as never)
    expect(await loadPersistedEditDraft('ds-1', 1, 'principal-one')).toBeUndefined()
    expect(await loadPersistedEditDraft('ds-2', 0, 'principal-one')).toBeUndefined()
    expect(await loadPersistedEditDraft('ds-1', 0, 'principal-two')).toBeUndefined()
  })

  it('persistEditDraft(null) deletes a previously stored draft', async () => {
    await persistEditDraft('ds-1', 0, 'principal-one', sampleOperations as never)
    await persistEditDraft('ds-1', 0, 'principal-one', null)
    expect(await loadPersistedEditDraft('ds-1', 0, 'principal-one')).toBeUndefined()
  })

  it('persistEditDraft(null) is a no-op when no draft exists', async () => {
    await expect(persistEditDraft('ds-1', 0, 'principal-one', null)).resolves.toBeUndefined()
    expect(await loadPersistedEditDraft('ds-1', 0, 'principal-one')).toBeUndefined()
  })

  it('purges unscoped legacy draft metadata before loading versioned drafts', async () => {
    await setMetadata('annotation-draft:ds-1:0:raw-user-id', { secret: true })

    await loadPersistedAnnotationDraft('ds-1', 0, 'principal-one')

    expect(await getMetadata('annotation-draft:ds-1:0:raw-user-id')).toBeUndefined()
  })
})

describe('draft merge behavior', () => {
  it('three-way merges non-overlapping fields and applies the selected overlap policy', () => {
    const baseline = { notes: 'base', nested: { score: 1, status: 'base' } }
    const local = { notes: 'local', nested: { score: 1, status: 'local' } }
    const server = { notes: 'server', nested: { score: 2, status: 'base' } }

    expect(mergeThreeWay(baseline, local, server, 'local')).toEqual({
      notes: 'local',
      nested: { score: 2, status: 'local' },
    })
    expect(mergeThreeWay(baseline, local, server, 'server').notes).toBe('server')
  })

  it('merges label additions and removals relative to the saved baseline', () => {
    expect(mergeLabelSets(['A', 'B'], ['B', 'LOCAL'], ['A', 'B', 'SERVER'])).toEqual([
      'B',
      'SERVER',
      'LOCAL',
    ])
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
    expect(await loadPersistedEditDraft('ds-1', 0, 'principal-one')).toBeUndefined()
  })

  it('round-trips operations through the fallback map', async () => {
    await persistEditDraft('ds-1', 0, 'principal-one', sampleOperations as never)
    expect((await loadPersistedEditDraft('ds-1', 0, 'principal-one'))?.draft).toEqual(
      sampleOperations,
    )
  })

  it('persistEditDraft(null) clears an entry in the fallback map', async () => {
    await persistEditDraft('ds-1', 0, 'principal-one', sampleOperations as never)
    await persistEditDraft('ds-1', 0, 'principal-one', null)
    expect(await loadPersistedEditDraft('ds-1', 0, 'principal-one')).toBeUndefined()
  })

  it('persistEditDraft(null) is a no-op in the fallback map when nothing stored', async () => {
    await expect(persistEditDraft('ds-1', 0, 'principal-one', null)).resolves.toBeUndefined()
  })

  it('clearPersistedEditDraftsForTests empties the fallback map', async () => {
    await persistEditDraft('ds-1', 0, 'principal-one', sampleOperations as never)
    await persistEditDraft('ds-2', 1, 'principal-one', sampleOperations as never)
    await clearPersistedEditDraftsForTests()
    expect(await loadPersistedEditDraft('ds-1', 0, 'principal-one')).toBeUndefined()
    expect(await loadPersistedEditDraft('ds-2', 1, 'principal-one')).toBeUndefined()
  })

  it('round-trips annotation drafts by dataset, episode, and annotator', async () => {
    const annotation = {
      annotatorId: 'reviewer',
      timestamp: '2026-09-11T00:00:00Z',
      taskCompleteness: { rating: 'unknown', confidence: 3 },
      trajectoryQuality: { overallScore: 3, metrics: {}, flags: [] },
      dataQuality: { overallQuality: 'good', issues: [] },
      anomalies: { anomalies: [] },
    }
    await persistAnnotationDraft('ds-1', 2, 'principal-one', {
      draft: annotation,
      baseline: annotation,
      baseEtag: '"revision-one"',
    } as never)

    const loaded = await loadPersistedAnnotationDraft('ds-1', 2, 'principal-one')
    expect(loaded).toMatchObject({
      schemaVersion: 2,
      principalScopeId: 'principal-one',
      baseEtag: '"revision-one"',
      draft: annotation,
      baseline: annotation,
    })
    expect(await loadPersistedAnnotationDraft('ds-1', 2, 'principal-two')).toBeUndefined()
  })

  it('round-trips label drafts by dataset', async () => {
    const draft = {
      availableLabels: ['SUCCESS', 'CUSTOM'],
      episodeLabels: { 0: ['CUSTOM'] },
      savedEpisodeLabels: { 0: ['SUCCESS'] },
    }
    await persistLabelDraft('ds-1', 'principal-one', { ...draft, baseEtag: '"labels-one"' })

    const loaded = await loadPersistedLabelDraft('ds-1', 'principal-one')
    expect(loaded).toMatchObject({ schemaVersion: 2, baseEtag: '"labels-one"' })
    expect(loaded?.draft).toEqual({
      availableLabels: draft.availableLabels,
      episodeLabels: draft.episodeLabels,
    })
    expect(await loadPersistedLabelDraft('ds-1', 'principal-two')).toBeUndefined()
  })

  it('rejects a stale generation without replacing the current envelope', async () => {
    const resource = { kind: 'episode-edit' as const, datasetId: 'ds-1', episodeIndex: 0 }
    await persistDraftEnvelope({
      principalScopeId: 'principal-one',
      resource,
      baseEtag: null,
      baseline: null,
      draft: sampleOperations,
      generation: 2,
    })

    await expect(
      persistDraftEnvelope({
        principalScopeId: 'principal-one',
        resource,
        baseEtag: null,
        baseline: null,
        draft: { ...sampleOperations, frameRemovals: [] },
        generation: 1,
      }),
    ).resolves.toBe(false)

    expect((await loadPersistedDraftEnvelope('principal-one', resource))?.generation).toBe(2)
  })
})
