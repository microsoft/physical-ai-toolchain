import { createDefaultSubtask, type SubtaskSegment, validateSegments } from '@/types/episode-edit'

type UpdateState<State> = (
  actionName: string,
  recipe: (state: State) => Partial<State>,
  options?: { validationErrors?: (nextState: State) => string[] },
) => void

interface FrameState {
  removedFrames: Set<number>
  insertedFrames: Map<number, { afterFrameIndex: number; interpolationFactor: number }>
  trajectoryAdjustments: Map<number, { frameIndex: number }>
}

interface SubtaskState {
  subtasks: SubtaskSegment[]
}

export function createEditStoreFrameActions<State extends FrameState>(
  updateState: UpdateState<State>,
) {
  return {
    toggleFrameRemoval: (frameIndex: number) => {
      updateState('toggleFrameRemoval', (state) => {
        const nextRemovedFrames = new Set(state.removedFrames)
        if (nextRemovedFrames.has(frameIndex)) {
          nextRemovedFrames.delete(frameIndex)
        } else {
          nextRemovedFrames.add(frameIndex)
        }
        return { removedFrames: nextRemovedFrames } as Partial<State>
      })
    },

    addFrameRange: (start: number, end: number) => {
      updateState('addFrameRange', (state) => {
        const nextRemovedFrames = new Set(state.removedFrames)
        for (let frameIndex = start; frameIndex <= end; frameIndex++) {
          nextRemovedFrames.add(frameIndex)
        }
        return { removedFrames: nextRemovedFrames } as Partial<State>
      })
    },

    addFramesByFrequency: (start: number, end: number, frequency: number) => {
      updateState('addFramesByFrequency', (state) => {
        const nextRemovedFrames = new Set(state.removedFrames)
        for (let frameIndex = start; frameIndex <= end; frameIndex += frequency) {
          nextRemovedFrames.add(frameIndex)
        }
        return { removedFrames: nextRemovedFrames } as Partial<State>
      })
    },

    removeFrameRange: (start: number, end: number) => {
      updateState('removeFrameRange', (state) => {
        const nextRemovedFrames = new Set(state.removedFrames)
        for (let frameIndex = start; frameIndex <= end; frameIndex++) {
          nextRemovedFrames.delete(frameIndex)
        }
        return { removedFrames: nextRemovedFrames } as Partial<State>
      })
    },

    clearRemovedFrames: () => {
      updateState(
        'clearRemovedFrames',
        () => ({ removedFrames: new Set<number>() }) as Partial<State>,
      )
    },

    insertFrame: (afterFrameIndex: number, factor = 0.5) => {
      updateState('insertFrame', (state) => {
        const nextInsertedFrames = new Map(state.insertedFrames)
        nextInsertedFrames.set(afterFrameIndex, {
          afterFrameIndex,
          interpolationFactor: factor,
        })
        return { insertedFrames: nextInsertedFrames } as Partial<State>
      })
    },

    removeInsertedFrame: (afterFrameIndex: number) => {
      updateState('removeInsertedFrame', (state) => {
        const nextInsertedFrames = new Map(state.insertedFrames)
        nextInsertedFrames.delete(afterFrameIndex)
        return { insertedFrames: nextInsertedFrames } as Partial<State>
      })
    },

    clearInsertedFrames: () => {
      updateState('clearInsertedFrames', () => ({ insertedFrames: new Map() }) as Partial<State>)
    },

    setTrajectoryAdjustment: (
      frameIndex: number,
      adjustment: Omit<{ frameIndex: number }, 'frameIndex'>,
    ) => {
      updateState('setTrajectoryAdjustment', (state) => {
        const nextTrajectoryAdjustments = new Map(state.trajectoryAdjustments)
        nextTrajectoryAdjustments.set(frameIndex, { ...adjustment, frameIndex })
        return { trajectoryAdjustments: nextTrajectoryAdjustments } as Partial<State>
      })
    },

    removeTrajectoryAdjustment: (frameIndex: number) => {
      updateState('removeTrajectoryAdjustment', (state) => {
        const nextTrajectoryAdjustments = new Map(state.trajectoryAdjustments)
        nextTrajectoryAdjustments.delete(frameIndex)
        return { trajectoryAdjustments: nextTrajectoryAdjustments } as Partial<State>
      })
    },

    clearTrajectoryAdjustments: () => {
      updateState(
        'clearTrajectoryAdjustments',
        () => ({ trajectoryAdjustments: new Map() }) as Partial<State>,
      )
    },
  }
}

export function createEditStoreSubtaskActions<State extends SubtaskState>(
  updateState: UpdateState<State>,
  getState: () => State,
) {
  return {
    addSubtask: (segment: SubtaskSegment) => {
      updateState(
        'addSubtask',
        (state) => ({ subtasks: [...state.subtasks, segment] }) as Partial<State>,
        { validationErrors: (nextState) => validateSegments(nextState.subtasks) },
      )
    },

    addSubtaskFromRange: (start: number, end: number) => {
      const { subtasks } = getState()
      const segment = createDefaultSubtask([start, end], subtasks)
      updateState(
        'addSubtaskFromRange',
        (state) => ({ subtasks: [...state.subtasks, segment] }) as Partial<State>,
        { validationErrors: (nextState) => validateSegments(nextState.subtasks) },
      )
    },

    updateSubtask: (id: string, update: Partial<SubtaskSegment>) => {
      updateState(
        'updateSubtask',
        (state) =>
          ({
            subtasks: state.subtasks.map((segment) =>
              segment.id === id ? { ...segment, ...update } : segment,
            ),
          }) as Partial<State>,
        { validationErrors: (nextState) => validateSegments(nextState.subtasks) },
      )
    },

    removeSubtask: (id: string) => {
      updateState(
        'removeSubtask',
        (state) =>
          ({ subtasks: state.subtasks.filter((segment) => segment.id !== id) }) as Partial<State>,
        { validationErrors: (nextState) => validateSegments(nextState.subtasks) },
      )
    },

    reorderSubtasks: (fromIndex: number, toIndex: number) => {
      updateState('reorderSubtasks', (state) => {
        const nextSubtasks = [...state.subtasks]
        const [removed] = nextSubtasks.splice(fromIndex, 1)
        nextSubtasks.splice(toIndex, 0, removed)
        return { subtasks: nextSubtasks } as Partial<State>
      })
    },
  }
}

export function createEditStoreTransformActions<
  State extends { globalTransform: unknown; cameraTransforms: Record<string, unknown> },
>(updateState: UpdateState<State>) {
  return {
    setGlobalTransform: (transform: State['globalTransform']) => {
      updateState('setGlobalTransform', () => ({ globalTransform: transform }) as Partial<State>)
    },

    setCameraTransform: (camera: string, transform: State['cameraTransforms'][string] | null) => {
      updateState('setCameraTransform', (state) => {
        const nextCameraTransforms = { ...state.cameraTransforms }
        if (transform) {
          nextCameraTransforms[camera] = transform
        } else {
          delete nextCameraTransforms[camera]
        }

        return { cameraTransforms: nextCameraTransforms } as Partial<State>
      })
    },

    clearTransforms: () => {
      updateState(
        'clearTransforms',
        () => ({ globalTransform: null, cameraTransforms: {} }) as Partial<State>,
      )
    },
  }
}
