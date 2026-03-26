import { useShallow } from 'zustand/react/shallow'

import { useEditStore } from './edit-store'

export const useTransformState = () =>
  useEditStore(
    useShallow((state) => ({
      globalTransform: state.globalTransform,
      cameraTransforms: state.cameraTransforms,
      setGlobalTransform: state.setGlobalTransform,
      setCameraTransform: state.setCameraTransform,
      clearTransforms: state.clearTransforms,
    })),
  )

export const useFrameRemovalState = () =>
  useEditStore(
    useShallow((state) => ({
      removedFrames: state.removedFrames,
      toggleFrameRemoval: state.toggleFrameRemoval,
      addFrameRange: state.addFrameRange,
      addFramesByFrequency: state.addFramesByFrequency,
      removeFrameRange: state.removeFrameRange,
      clearRemovedFrames: state.clearRemovedFrames,
    })),
  )

export const useFrameInsertionState = () =>
  useEditStore(
    useShallow((state) => ({
      insertedFrames: state.insertedFrames,
      insertFrame: state.insertFrame,
      removeInsertedFrame: state.removeInsertedFrame,
      clearInsertedFrames: state.clearInsertedFrames,
    })),
  )

export const useSubtaskState = () =>
  useEditStore(
    useShallow((state) => ({
      subtasks: state.subtasks,
      validationErrors: state.validationErrors,
      addSubtask: state.addSubtask,
      addSubtaskFromRange: state.addSubtaskFromRange,
      updateSubtask: state.updateSubtask,
      removeSubtask: state.removeSubtask,
      reorderSubtasks: state.reorderSubtasks,
    })),
  )

export const useEditDirtyState = () =>
  useEditStore(
    useShallow((state) => ({
      isDirty: state.isDirty,
      markSaved: state.markSaved,
      resetEdits: state.resetEdits,
    })),
  )

export const useTrajectoryAdjustmentState = () =>
  useEditStore(
    useShallow((state) => ({
      trajectoryAdjustments: state.trajectoryAdjustments,
      setTrajectoryAdjustment: state.setTrajectoryAdjustment,
      removeTrajectoryAdjustment: state.removeTrajectoryAdjustment,
      getTrajectoryAdjustment: state.getTrajectoryAdjustment,
      clearTrajectoryAdjustments: state.clearTrajectoryAdjustments,
    })),
  )
