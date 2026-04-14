import { describe, expect, it, vi } from 'vitest'

import {
  buildDraftPersistencePayload,
  buildEditOperations,
  buildEditStateUpdate,
  buildOriginalEditState,
  computeDirty,
  hasEditContent,
  persistEditStateDraft,
} from '../edit-store-helpers'

vi.mock('@/lib/edit-draft-storage', () => ({ persistEditDraft: vi.fn() }))

function makeBaseState() {
  return {
    datasetId: 'ds-1' as string | null,
    episodeIndex: 0 as number | null,
    globalTransform: null,
    cameraTransforms: {} as Record<
      string,
      { crop?: { x: number; y: number; width: number; height: number } }
    >,
    removedFrames: new Set<number>(),
    insertedFrames: new Map<number, { afterFrameIndex: number; interpolationFactor: number }>(),
    subtasks: [] as {
      id: string
      label: string
      frameRange: [number, number]
      color: string
      source: 'manual' | 'auto'
    }[],
    trajectoryAdjustments: new Map<
      number,
      { frameIndex: number; rightArmDelta?: [number, number, number] }
    >(),
  }
}

function makeFullState(overrides: Record<string, unknown> = {}) {
  const base = makeBaseState()
  const originalState = {
    globalTransform: null,
    cameraTransforms: {},
    removedFrames: new Set<number>(),
    insertedFrames: new Map(),
    subtasks: [],
    trajectoryAdjustments: new Map(),
  }
  return {
    ...base,
    originalState,
    isDirty: false,
    validationErrors: [] as string[],
    savedEpisodeDrafts: {},
    ...overrides,
  }
}

describe('edit-store-helpers', () => {
  it('recomputes dirty and validation state from the provided state update', () => {
    const originalState = {
      globalTransform: null,
      cameraTransforms: {},
      removedFrames: new Set<number>(),
      insertedFrames: new Map(),
      subtasks: [],
      trajectoryAdjustments: new Map(),
    }

    const nextState = buildEditStateUpdate(
      {
        datasetId: 'dataset-1',
        episodeIndex: 0,
        globalTransform: null,
        cameraTransforms: {},
        removedFrames: new Set<number>(),
        insertedFrames: new Map(),
        subtasks: [],
        trajectoryAdjustments: new Map(),
        originalState,
        isDirty: false,
        validationErrors: [],
        savedEpisodeDrafts: {},
      },
      { removedFrames: new Set([4]) },
      { validationErrors: ['overlap'] },
    )

    expect(nextState.isDirty).toBe(true)
    expect(nextState.validationErrors).toEqual(['overlap'])
    expect(nextState.removedFrames.has(4)).toBe(true)
  })

  it('creates a persistable draft payload only when the edit state has episode identity', () => {
    expect(
      buildDraftPersistencePayload({
        datasetId: null,
        episodeIndex: null,
        globalTransform: null,
        cameraTransforms: {},
        removedFrames: new Set<number>(),
        insertedFrames: new Map(),
        subtasks: [],
        trajectoryAdjustments: new Map(),
      }),
    ).toEqual({ datasetId: null, episodeIndex: null, operations: null, persistedDraft: null })

    const payload = buildDraftPersistencePayload({
      datasetId: 'dataset-1',
      episodeIndex: 3,
      globalTransform: null,
      cameraTransforms: {},
      removedFrames: new Set([5]),
      insertedFrames: new Map(),
      subtasks: [],
      trajectoryAdjustments: new Map(),
    })

    expect(payload.datasetId).toBe('dataset-1')
    expect(payload.episodeIndex).toBe(3)
    expect(payload.operations).toMatchObject({
      datasetId: 'dataset-1',
      episodeIndex: 3,
      removedFrames: [5],
    })
    expect(payload.persistedDraft).toMatchObject({
      datasetId: 'dataset-1',
      episodeIndex: 3,
      removedFrames: [5],
    })
  })

  describe('buildOriginalEditState', () => {
    it('produces deep copies independent of the source state', () => {
      const source = makeBaseState()
      source.removedFrames.add(1)
      source.insertedFrames.set(5, { afterFrameIndex: 5, interpolationFactor: 0.5 })
      source.cameraTransforms = { cam0: { crop: { x: 0, y: 0, width: 100, height: 100 } } }

      const snapshot = buildOriginalEditState(source)

      source.removedFrames.add(99)
      source.insertedFrames.set(10, { afterFrameIndex: 10, interpolationFactor: 0.3 })
      source.cameraTransforms.cam0 = { crop: { x: 50, y: 50, width: 50, height: 50 } }

      expect(snapshot.removedFrames.has(99)).toBe(false)
      expect(snapshot.insertedFrames.has(10)).toBe(false)
      expect(snapshot.cameraTransforms.cam0).toEqual({
        crop: { x: 0, y: 0, width: 100, height: 100 },
      })
    })

    it('handles empty collections', () => {
      const snapshot = buildOriginalEditState(makeBaseState())

      expect(snapshot.removedFrames.size).toBe(0)
      expect(snapshot.insertedFrames.size).toBe(0)
      expect(snapshot.subtasks).toEqual([])
      expect(snapshot.trajectoryAdjustments.size).toBe(0)
    })
  })

  describe('buildEditOperations', () => {
    it('returns null when datasetId is null', () => {
      const state = makeBaseState()
      state.datasetId = null

      expect(buildEditOperations(state)).toBeNull()
    })

    it('returns null when episodeIndex is null', () => {
      const state = makeBaseState()
      state.episodeIndex = null

      expect(buildEditOperations(state)).toBeNull()
    })

    it('returns sorted removedFrames from unsorted Set', () => {
      const state = makeBaseState()
      state.removedFrames = new Set([10, 2, 7, 1])

      const ops = buildEditOperations(state)!

      expect(ops.removedFrames).toEqual([1, 2, 7, 10])
    })

    it('returns insertedFrames sorted by afterFrameIndex', () => {
      const state = makeBaseState()
      state.insertedFrames.set(20, { afterFrameIndex: 20, interpolationFactor: 0.5 })
      state.insertedFrames.set(3, { afterFrameIndex: 3, interpolationFactor: 0.8 })
      state.insertedFrames.set(10, { afterFrameIndex: 10, interpolationFactor: 0.2 })

      const ops = buildEditOperations(state)!

      expect(ops.insertedFrames).toEqual([
        { afterFrameIndex: 3, interpolationFactor: 0.8 },
        { afterFrameIndex: 10, interpolationFactor: 0.2 },
        { afterFrameIndex: 20, interpolationFactor: 0.5 },
      ])
    })

    it('omits empty fields as undefined', () => {
      const ops = buildEditOperations(makeBaseState())!

      expect(ops.globalTransform).toBeUndefined()
      expect(ops.cameraTransforms).toBeUndefined()
      expect(ops.removedFrames).toBeUndefined()
      expect(ops.insertedFrames).toBeUndefined()
      expect(ops.subtasks).toBeUndefined()
      expect(ops.trajectoryAdjustments).toBeUndefined()
    })
  })

  describe('hasEditContent', () => {
    it('returns false when all fields are undefined', () => {
      expect(hasEditContent({ datasetId: 'ds', episodeIndex: 0 })).toBe(false)
    })

    it('returns true when removedFrames is present', () => {
      expect(hasEditContent({ datasetId: 'ds', episodeIndex: 0, removedFrames: [1] })).toBe(true)
    })

    it('returns true when trajectoryAdjustments is present', () => {
      expect(
        hasEditContent({
          datasetId: 'ds',
          episodeIndex: 0,
          trajectoryAdjustments: [{ frameIndex: 0, rightArmDelta: [0.1, 0, 0] }],
        }),
      ).toBe(true)
    })
  })

  describe('computeDirty', () => {
    it('returns false when originalState is null', () => {
      const state = makeFullState({ originalState: null })

      expect(computeDirty(state)).toBe(false)
    })

    it('returns false when all fields match', () => {
      expect(computeDirty(makeFullState())).toBe(false)
    })

    it('returns true for globalTransform diff', () => {
      const state = makeFullState({
        globalTransform: { crop: { x: 0, y: 0, width: 50, height: 50 } },
      })

      expect(computeDirty(state)).toBe(true)
    })

    it('returns true for removedFrames size diff', () => {
      const state = makeFullState({ removedFrames: new Set([3]) })

      expect(computeDirty(state)).toBe(true)
    })

    it('returns true for removedFrames content diff', () => {
      const state = makeFullState({
        removedFrames: new Set([5]),
        originalState: {
          globalTransform: null,
          cameraTransforms: {},
          removedFrames: new Set([9]),
          insertedFrames: new Map(),
          subtasks: [],
          trajectoryAdjustments: new Map(),
        },
      })

      expect(computeDirty(state)).toBe(true)
    })

    it('returns true for insertedFrames interpolationFactor diff', () => {
      const original = new Map([[1, { afterFrameIndex: 1, interpolationFactor: 0.5 }]])
      const current = new Map([[1, { afterFrameIndex: 1, interpolationFactor: 0.9 }]])

      const state = makeFullState({
        insertedFrames: current,
        originalState: {
          globalTransform: null,
          cameraTransforms: {},
          removedFrames: new Set(),
          insertedFrames: original,
          subtasks: [],
          trajectoryAdjustments: new Map(),
        },
      })

      expect(computeDirty(state)).toBe(true)
    })

    it('returns true for trajectoryAdjustments diff', () => {
      const state = makeFullState({
        trajectoryAdjustments: new Map([
          [0, { frameIndex: 0, rightArmDelta: [0.1, 0, 0] as [number, number, number] }],
        ]),
      })

      expect(computeDirty(state)).toBe(true)
    })
  })

  describe('persistEditStateDraft', () => {
    it('calls persistEditDraft with correct args when identity and content exist', async () => {
      const { persistEditDraft } = await import('@/lib/edit-draft-storage')

      const state = makeBaseState()
      state.removedFrames = new Set([2, 4])

      await persistEditStateDraft(state)

      expect(vi.mocked(persistEditDraft)).toHaveBeenCalledWith(
        'ds-1',
        0,
        expect.objectContaining({ datasetId: 'ds-1', episodeIndex: 0, removedFrames: [2, 4] }),
      )
    })

    it('returns early when datasetId is null', async () => {
      const { persistEditDraft } = await import('@/lib/edit-draft-storage')
      vi.mocked(persistEditDraft).mockClear()

      const state = makeBaseState()
      state.datasetId = null

      await persistEditStateDraft(state)

      expect(vi.mocked(persistEditDraft)).not.toHaveBeenCalled()
    })
  })
})
