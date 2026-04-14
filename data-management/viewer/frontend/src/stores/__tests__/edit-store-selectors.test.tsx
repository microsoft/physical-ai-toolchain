import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useEditStore } from '../edit-store'
import {
  useEditDirtyState,
  useFrameInsertionState,
  useFrameRemovalState,
  useSubtaskState,
  useTrajectoryAdjustmentState,
  useTransformState,
} from '../edit-store-selectors'

vi.mock('@/lib/edit-draft-storage', () => ({
  persistEditDraft: vi.fn(),
}))

vi.mock('@/types/episode-edit', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/types/episode-edit')>()
  return {
    ...actual,
    validateSegments: vi.fn(() => []),
    createDefaultSubtask: vi.fn((range: [number, number]) => ({
      id: `subtask-${range[0]}-${range[1]}`,
      label: '',
      frameRange: range,
    })),
  }
})

describe('edit-store-selectors', () => {
  beforeEach(() => {
    useEditStore.getState().clear()
  })

  describe('useTransformState', () => {
    it('exposes initial transform values and actions', () => {
      const { result } = renderHook(() => useTransformState())

      expect(result.current.globalTransform).toBeNull()
      expect(result.current.cameraTransforms).toEqual({})
      expect(result.current.setGlobalTransform).toBeTypeOf('function')
      expect(result.current.setCameraTransform).toBeTypeOf('function')
      expect(result.current.clearTransforms).toBeTypeOf('function')
    })

    it('updates global transform through the selector', () => {
      const { result } = renderHook(() => useTransformState())

      act(() => result.current.setGlobalTransform({ rotation: 90 } as never))

      expect(result.current.globalTransform).toEqual({ rotation: 90 })
    })

    it('manages per-camera transforms', () => {
      const { result } = renderHook(() => useTransformState())

      act(() => result.current.setCameraTransform('front', { rotation: 180 } as never))
      expect(result.current.cameraTransforms.front).toEqual({ rotation: 180 })

      act(() => result.current.setCameraTransform('front', null as never))
      expect(result.current.cameraTransforms.front).toBeUndefined()
    })

    it('clears all transforms at once', () => {
      const { result } = renderHook(() => useTransformState())

      act(() => {
        result.current.setGlobalTransform({ rotation: 90 } as never)
        result.current.setCameraTransform('wrist', { rotation: 45 } as never)
      })
      act(() => result.current.clearTransforms())

      expect(result.current.globalTransform).toBeNull()
      expect(result.current.cameraTransforms).toEqual({})
    })
  })

  describe('useFrameRemovalState', () => {
    it('starts with an empty removed frames set', () => {
      const { result } = renderHook(() => useFrameRemovalState())

      expect(result.current.removedFrames.size).toBe(0)
    })

    it('toggles frame removal on and off', () => {
      const { result } = renderHook(() => useFrameRemovalState())

      act(() => result.current.toggleFrameRemoval(5))
      expect(result.current.removedFrames.has(5)).toBe(true)

      act(() => result.current.toggleFrameRemoval(5))
      expect(result.current.removedFrames.has(5)).toBe(false)
    })

    it('adds and clears a frame range', () => {
      const { result } = renderHook(() => useFrameRemovalState())

      act(() => result.current.addFrameRange(0, 3))
      expect(Array.from(result.current.removedFrames).sort()).toEqual([0, 1, 2, 3])

      act(() => result.current.clearRemovedFrames())
      expect(result.current.removedFrames.size).toBe(0)
    })
  })

  describe('useFrameInsertionState', () => {
    it('starts with an empty inserted frames map', () => {
      const { result } = renderHook(() => useFrameInsertionState())

      expect(result.current.insertedFrames.size).toBe(0)
    })

    it('inserts a frame with default interpolation factor', () => {
      const { result } = renderHook(() => useFrameInsertionState())

      act(() => result.current.insertFrame(3))

      expect(result.current.insertedFrames.has(3)).toBe(true)
      expect(result.current.insertedFrames.get(3)?.interpolationFactor).toBe(0.5)
    })

    it('removes an inserted frame', () => {
      const { result } = renderHook(() => useFrameInsertionState())

      act(() => result.current.insertFrame(2))
      act(() => result.current.removeInsertedFrame(2))

      expect(result.current.insertedFrames.has(2)).toBe(false)
    })
  })

  describe('useSubtaskState', () => {
    it('starts with empty subtasks and no validation errors', () => {
      const { result } = renderHook(() => useSubtaskState())

      expect(result.current.subtasks).toEqual([])
      expect(result.current.validationErrors).toEqual([])
    })

    it('adds and removes a subtask', () => {
      const { result } = renderHook(() => useSubtaskState())
      const segment = { id: 's1', label: 'Pick', frameRange: [0, 10] as [number, number] }

      act(() => result.current.addSubtask(segment as never))
      expect(result.current.subtasks).toHaveLength(1)

      act(() => result.current.removeSubtask('s1'))
      expect(result.current.subtasks).toHaveLength(0)
    })
  })

  describe('useEditDirtyState', () => {
    it('reports clean state initially', () => {
      const { result } = renderHook(() => useEditDirtyState())

      expect(result.current.isDirty).toBe(false)
      expect(result.current.markSaved).toBeTypeOf('function')
      expect(result.current.resetEdits).toBeTypeOf('function')
    })
  })

  describe('useTrajectoryAdjustmentState', () => {
    it('starts with an empty trajectory adjustments map', () => {
      const { result } = renderHook(() => useTrajectoryAdjustmentState())

      expect(result.current.trajectoryAdjustments.size).toBe(0)
    })

    it('sets and removes a trajectory adjustment', () => {
      const { result } = renderHook(() => useTrajectoryAdjustmentState())

      act(() => result.current.setTrajectoryAdjustment(5, { frameIndex: 5 } as never))
      expect(result.current.trajectoryAdjustments.has(5)).toBe(true)

      act(() => result.current.removeTrajectoryAdjustment(5))
      expect(result.current.trajectoryAdjustments.has(5)).toBe(false)
    })

    it('clears all trajectory adjustments', () => {
      const { result } = renderHook(() => useTrajectoryAdjustmentState())

      act(() => {
        result.current.setTrajectoryAdjustment(1, { frameIndex: 1 } as never)
        result.current.setTrajectoryAdjustment(2, { frameIndex: 2 } as never)
      })
      act(() => result.current.clearTrajectoryAdjustments())

      expect(result.current.trajectoryAdjustments.size).toBe(0)
    })
  })
})
