import { describe, expect, it, vi } from 'vitest'

import { createEditStoreFrameActions } from '../edit-store-action-factories'

describe('edit-store-action-factories', () => {
  it('updates removed frames through the shared updateState callback', () => {
    const updateState = vi.fn((actionName, recipe) => {
      const next = recipe({
        removedFrames: new Set<number>(),
        insertedFrames: new Map<number, { afterFrameIndex: number; interpolationFactor: number }>(),
        trajectoryAdjustments: new Map<number, { frameIndex: number; rightArmDelta?: [number, number, number] }>(),
      })

      return { actionName, next }
    })

    const actions = createEditStoreFrameActions(updateState)
    actions.addFrameRange(2, 4)

    expect(updateState).toHaveBeenCalledWith('addFrameRange', expect.any(Function))

    const result = updateState.mock.results[0]?.value as { next: { removedFrames: Set<number> } }
    expect(Array.from(result.next.removedFrames)).toEqual([2, 3, 4])
  })
})
