import { describe, expect, it, vi } from 'vitest'

import type { SubtaskSegment } from '@/types/episode-edit'

vi.mock('@/types/episode-edit', async (importOriginal) => {
  const orig = await importOriginal<typeof import('@/types/episode-edit')>()
  return {
    ...orig,
    validateSegments: vi.fn(() => []),
    createDefaultSubtask: vi.fn(
      (range: [number, number], existing: unknown[]) =>
        ({
          id: 'new-subtask',
          label: `Subtask ${(existing as unknown[]).length + 1}`,
          frameRange: range,
          color: '#ff0000',
          source: 'manual' as const,
        }) satisfies SubtaskSegment,
    ),
  }
})

import {
  createEditStoreFrameActions,
  createEditStoreSubtaskActions,
  createEditStoreTransformActions,
} from '../edit-store-action-factories'

function createFrameUpdateState(
  initialState?: Partial<{
    removedFrames: Set<number>
    insertedFrames: Map<number, { afterFrameIndex: number; interpolationFactor: number }>
    trajectoryAdjustments: Map<number, { frameIndex: number }>
  }>,
) {
  const state = {
    removedFrames: new Set<number>(),
    insertedFrames: new Map<number, { afterFrameIndex: number; interpolationFactor: number }>(),
    trajectoryAdjustments: new Map<number, { frameIndex: number }>(),
    ...initialState,
  }
  return vi.fn((actionName: string, recipe: (s: typeof state) => Partial<typeof state>) => {
    const updates = recipe(state)
    Object.assign(state, updates)
    return { actionName, state: { ...state } }
  })
}

function createSubtaskUpdateState(initialSubtasks: SubtaskSegment[] = []) {
  let state = { subtasks: initialSubtasks }
  const updateState = vi.fn(
    (
      actionName: string,
      recipe: (s: typeof state) => Partial<typeof state>,
      options?: { validationErrors?: (s: typeof state) => string[] },
    ) => {
      const updates = recipe(state)
      state = { ...state, ...updates }
      if (options?.validationErrors) options.validationErrors(state)
      return { actionName, state: { ...state } }
    },
  )
  const getState = () => state
  return { updateState, getState }
}

function createTransformUpdateState(
  initialState?: Partial<{
    globalTransform: unknown
    cameraTransforms: Record<string, unknown>
  }>,
) {
  const state = {
    globalTransform: null as unknown,
    cameraTransforms: {} as Record<string, unknown>,
    ...initialState,
  }
  return vi.fn((actionName: string, recipe: (s: typeof state) => Partial<typeof state>) => {
    const updates = recipe(state)
    Object.assign(state, updates)
    return { actionName, state: { ...state } }
  })
}

describe('edit-store-action-factories', () => {
  describe('createEditStoreFrameActions', () => {
    it('updates removed frames through the shared updateState callback', () => {
      const updateState = vi.fn((actionName, recipe) => {
        const next = recipe({
          removedFrames: new Set<number>(),
          insertedFrames: new Map<
            number,
            { afterFrameIndex: number; interpolationFactor: number }
          >(),
          trajectoryAdjustments: new Map<
            number,
            { frameIndex: number; rightArmDelta?: [number, number, number] }
          >(),
        })

        return { actionName, next }
      })

      const actions = createEditStoreFrameActions(updateState)
      actions.addFrameRange(2, 4)

      expect(updateState).toHaveBeenCalledWith('addFrameRange', expect.any(Function))

      const result = updateState.mock.results[0]?.value as {
        next: { removedFrames: Set<number> }
      }
      expect(Array.from(result.next.removedFrames)).toEqual([2, 3, 4])
    })

    it('toggleFrameRemoval adds frame when not present', () => {
      const updateState = createFrameUpdateState()
      const actions = createEditStoreFrameActions(updateState)

      actions.toggleFrameRemoval(5)

      const result = updateState.mock.results[0]?.value as {
        state: { removedFrames: Set<number> }
      }
      expect(result.state.removedFrames.has(5)).toBe(true)
    })

    it('toggleFrameRemoval removes frame when present', () => {
      const updateState = createFrameUpdateState({ removedFrames: new Set([5]) })
      const actions = createEditStoreFrameActions(updateState)

      actions.toggleFrameRemoval(5)

      const result = updateState.mock.results[0]?.value as {
        state: { removedFrames: Set<number> }
      }
      expect(result.state.removedFrames.has(5)).toBe(false)
    })

    it('addFramesByFrequency adds frames at frequency intervals', () => {
      const updateState = createFrameUpdateState()
      const actions = createEditStoreFrameActions(updateState)

      actions.addFramesByFrequency(0, 10, 3)

      const result = updateState.mock.results[0]?.value as {
        state: { removedFrames: Set<number> }
      }
      expect(Array.from(result.state.removedFrames).sort()).toEqual([0, 3, 6, 9])
    })

    it('removeFrameRange removes range from existing set', () => {
      const updateState = createFrameUpdateState({ removedFrames: new Set([1, 2, 3, 4, 5]) })
      const actions = createEditStoreFrameActions(updateState)

      actions.removeFrameRange(2, 4)

      const result = updateState.mock.results[0]?.value as {
        state: { removedFrames: Set<number> }
      }
      expect(Array.from(result.state.removedFrames).sort()).toEqual([1, 5])
    })

    it('clearRemovedFrames produces empty set', () => {
      const updateState = createFrameUpdateState({ removedFrames: new Set([1, 2, 3]) })
      const actions = createEditStoreFrameActions(updateState)

      actions.clearRemovedFrames()

      const result = updateState.mock.results[0]?.value as {
        state: { removedFrames: Set<number> }
      }
      expect(result.state.removedFrames.size).toBe(0)
    })

    it('insertFrame inserts with default 0.5 factor', () => {
      const updateState = createFrameUpdateState()
      const actions = createEditStoreFrameActions(updateState)

      actions.insertFrame(3)

      const result = updateState.mock.results[0]?.value as {
        state: {
          insertedFrames: Map<number, { afterFrameIndex: number; interpolationFactor: number }>
        }
      }
      expect(result.state.insertedFrames.get(3)).toEqual({
        afterFrameIndex: 3,
        interpolationFactor: 0.5,
      })
    })

    it('insertFrame inserts with custom factor', () => {
      const updateState = createFrameUpdateState()
      const actions = createEditStoreFrameActions(updateState)

      actions.insertFrame(3, 0.75)

      const result = updateState.mock.results[0]?.value as {
        state: {
          insertedFrames: Map<number, { afterFrameIndex: number; interpolationFactor: number }>
        }
      }
      expect(result.state.insertedFrames.get(3)).toEqual({
        afterFrameIndex: 3,
        interpolationFactor: 0.75,
      })
    })

    it('removeInsertedFrame removes insertion by key', () => {
      const updateState = createFrameUpdateState({
        insertedFrames: new Map([[3, { afterFrameIndex: 3, interpolationFactor: 0.5 }]]),
      })
      const actions = createEditStoreFrameActions(updateState)

      actions.removeInsertedFrame(3)

      const result = updateState.mock.results[0]?.value as {
        state: {
          insertedFrames: Map<number, { afterFrameIndex: number; interpolationFactor: number }>
        }
      }
      expect(result.state.insertedFrames.has(3)).toBe(false)
    })

    it('clearInsertedFrames produces empty map', () => {
      const updateState = createFrameUpdateState({
        insertedFrames: new Map([[1, { afterFrameIndex: 1, interpolationFactor: 0.5 }]]),
      })
      const actions = createEditStoreFrameActions(updateState)

      actions.clearInsertedFrames()

      const result = updateState.mock.results[0]?.value as {
        state: {
          insertedFrames: Map<number, { afterFrameIndex: number; interpolationFactor: number }>
        }
      }
      expect(result.state.insertedFrames.size).toBe(0)
    })

    it('setTrajectoryAdjustment adds adjustment with frameIndex merged', () => {
      const updateState = createFrameUpdateState()
      const actions = createEditStoreFrameActions(updateState)

      actions.setTrajectoryAdjustment(7, {})

      const result = updateState.mock.results[0]?.value as {
        state: { trajectoryAdjustments: Map<number, { frameIndex: number }> }
      }
      expect(result.state.trajectoryAdjustments.get(7)).toEqual({ frameIndex: 7 })
    })

    it('removeTrajectoryAdjustment removes by key', () => {
      const updateState = createFrameUpdateState({
        trajectoryAdjustments: new Map([[7, { frameIndex: 7 }]]),
      })
      const actions = createEditStoreFrameActions(updateState)

      actions.removeTrajectoryAdjustment(7)

      const result = updateState.mock.results[0]?.value as {
        state: { trajectoryAdjustments: Map<number, { frameIndex: number }> }
      }
      expect(result.state.trajectoryAdjustments.has(7)).toBe(false)
    })

    it('clearTrajectoryAdjustments produces empty map', () => {
      const updateState = createFrameUpdateState({
        trajectoryAdjustments: new Map([[1, { frameIndex: 1 }]]),
      })
      const actions = createEditStoreFrameActions(updateState)

      actions.clearTrajectoryAdjustments()

      const result = updateState.mock.results[0]?.value as {
        state: { trajectoryAdjustments: Map<number, { frameIndex: number }> }
      }
      expect(result.state.trajectoryAdjustments.size).toBe(0)
    })
  })

  describe('createEditStoreSubtaskActions', () => {
    const subtask: SubtaskSegment = {
      id: 'sub-1',
      label: 'Pick',
      frameRange: [0, 10],
      color: '#3b82f6',
      source: 'manual' as const,
    }

    it('addSubtask appends segment and calls validation', async () => {
      const { validateSegments } = vi.mocked(await import('@/types/episode-edit'))
      const { updateState, getState } = createSubtaskUpdateState()
      const actions = createEditStoreSubtaskActions(updateState, getState)

      actions.addSubtask(subtask)

      const result = updateState.mock.results[0]?.value as {
        state: { subtasks: SubtaskSegment[] }
      }
      expect(result.state.subtasks).toHaveLength(1)
      expect(result.state.subtasks[0]).toEqual(subtask)
      expect(validateSegments).toHaveBeenCalled()
    })

    it('addSubtaskFromRange creates default subtask from range', async () => {
      const { createDefaultSubtask } = vi.mocked(await import('@/types/episode-edit'))
      const { updateState, getState } = createSubtaskUpdateState()
      const actions = createEditStoreSubtaskActions(updateState, getState)

      actions.addSubtaskFromRange(5, 15)

      expect(createDefaultSubtask).toHaveBeenCalledWith([5, 15], [])
      const result = updateState.mock.results[0]?.value as {
        state: { subtasks: SubtaskSegment[] }
      }
      expect(result.state.subtasks).toHaveLength(1)
      expect(result.state.subtasks[0]?.frameRange).toEqual([5, 15])
    })

    it('updateSubtask updates matching segment by id', () => {
      const { updateState, getState } = createSubtaskUpdateState([subtask])
      const actions = createEditStoreSubtaskActions(updateState, getState)

      actions.updateSubtask('sub-1', { label: 'Place' })

      const result = updateState.mock.results[0]?.value as {
        state: { subtasks: SubtaskSegment[] }
      }
      expect(result.state.subtasks[0]?.label).toBe('Place')
      expect(result.state.subtasks[0]?.id).toBe('sub-1')
    })

    it('removeSubtask filters out segment by id', () => {
      const second: SubtaskSegment = {
        id: 'sub-2',
        label: 'Place',
        frameRange: [20, 30],
        color: '#10b981',
        source: 'manual' as const,
      }
      const { updateState, getState } = createSubtaskUpdateState([subtask, second])
      const actions = createEditStoreSubtaskActions(updateState, getState)

      actions.removeSubtask('sub-1')

      const result = updateState.mock.results[0]?.value as {
        state: { subtasks: SubtaskSegment[] }
      }
      expect(result.state.subtasks).toHaveLength(1)
      expect(result.state.subtasks[0]?.id).toBe('sub-2')
    })

    it('reorderSubtasks moves element from fromIndex to toIndex', () => {
      const second: SubtaskSegment = {
        id: 'sub-2',
        label: 'Place',
        frameRange: [20, 30],
        color: '#10b981',
        source: 'manual' as const,
      }
      const { updateState, getState } = createSubtaskUpdateState([subtask, second])
      const actions = createEditStoreSubtaskActions(updateState, getState)

      actions.reorderSubtasks(0, 1)

      const result = updateState.mock.results[0]?.value as {
        state: { subtasks: SubtaskSegment[] }
      }
      expect(result.state.subtasks[0]?.id).toBe('sub-2')
      expect(result.state.subtasks[1]?.id).toBe('sub-1')
    })
  })

  describe('createEditStoreTransformActions', () => {
    it('setGlobalTransform sets transform value', () => {
      const updateState = createTransformUpdateState()
      const actions = createEditStoreTransformActions(updateState)

      actions.setGlobalTransform({ scale: 2 })

      const result = updateState.mock.results[0]?.value as {
        state: { globalTransform: unknown }
      }
      expect(result.state.globalTransform).toEqual({ scale: 2 })
    })

    it('setCameraTransform sets camera transform and deletes when null', () => {
      const updateState = createTransformUpdateState({
        cameraTransforms: { cam1: { offset: 1 } },
      })
      const actions = createEditStoreTransformActions(updateState)

      actions.setCameraTransform('cam2', { offset: 2 })
      let result = updateState.mock.results[0]?.value as {
        state: { cameraTransforms: Record<string, unknown> }
      }
      expect(result.state.cameraTransforms['cam2']).toEqual({ offset: 2 })

      actions.setCameraTransform('cam1', null)
      result = updateState.mock.results[1]?.value as {
        state: { cameraTransforms: Record<string, unknown> }
      }
      expect(result.state.cameraTransforms['cam1']).toBeUndefined()
    })

    it('clearTransforms resets to null and empty object', () => {
      const updateState = createTransformUpdateState({
        globalTransform: { scale: 2 },
        cameraTransforms: { cam1: { offset: 1 } },
      })
      const actions = createEditStoreTransformActions(updateState)

      actions.clearTransforms()

      const result = updateState.mock.results[0]?.value as {
        state: { globalTransform: unknown; cameraTransforms: Record<string, unknown> }
      }
      expect(result.state.globalTransform).toBeNull()
      expect(result.state.cameraTransforms).toEqual({})
    })
  })
})
