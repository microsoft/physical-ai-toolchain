import { describe, expect, it } from 'vitest'

import {
  buildDraftPersistencePayload,
  buildEditStateUpdate,
} from '../edit-store-helpers'

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
    expect(buildDraftPersistencePayload({
      datasetId: null,
      episodeIndex: null,
      globalTransform: null,
      cameraTransforms: {},
      removedFrames: new Set<number>(),
      insertedFrames: new Map(),
      subtasks: [],
      trajectoryAdjustments: new Map(),
    })).toEqual({ datasetId: null, episodeIndex: null, operations: null, persistedDraft: null })

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
    expect(payload.operations).toMatchObject({ datasetId: 'dataset-1', episodeIndex: 3, removedFrames: [5] })
    expect(payload.persistedDraft).toMatchObject({ datasetId: 'dataset-1', episodeIndex: 3, removedFrames: [5] })
  })
})
