import { persistEditDraft } from '@/lib/edit-draft-storage'
import type {
  EpisodeEditOperations,
  FrameInsertion,
  ImageTransform,
  SubtaskSegment,
  TrajectoryAdjustment,
} from '@/types/episode-edit'

export interface EditOriginalStateSnapshot {
  globalTransform: ImageTransform | null
  cameraTransforms: Record<string, ImageTransform>
  removedFrames: Set<number>
  insertedFrames: Map<number, FrameInsertion>
  subtasks: SubtaskSegment[]
  trajectoryAdjustments: Map<number, TrajectoryAdjustment>
}

export interface EditStateSnapshot {
  datasetId: string | null
  episodeIndex: number | null
  globalTransform: ImageTransform | null
  cameraTransforms: Record<string, ImageTransform>
  removedFrames: Set<number>
  insertedFrames: Map<number, FrameInsertion>
  subtasks: SubtaskSegment[]
  trajectoryAdjustments: Map<number, TrajectoryAdjustment>
}

export interface EditStateWithDerived extends EditStateSnapshot {
  originalState: EditOriginalStateSnapshot | null
  isDirty: boolean
  validationErrors: string[]
  savedEpisodeDrafts: Record<string, EpisodeEditOperations>
}

interface EditStateUpdateOptions {
  validationErrors?: string[]
}

export function buildOriginalEditState(
  state: Pick<
    EditStateSnapshot,
    | 'globalTransform'
    | 'cameraTransforms'
    | 'removedFrames'
    | 'insertedFrames'
    | 'subtasks'
    | 'trajectoryAdjustments'
  >,
): EditOriginalStateSnapshot {
  return {
    globalTransform: state.globalTransform,
    cameraTransforms: structuredClone(state.cameraTransforms),
    removedFrames: new Set(state.removedFrames),
    insertedFrames: new Map(state.insertedFrames),
    subtasks: structuredClone(state.subtasks),
    trajectoryAdjustments: new Map(state.trajectoryAdjustments),
  }
}

export function buildEditOperations(state: EditStateSnapshot): EpisodeEditOperations | null {
  if (!state.datasetId || state.episodeIndex === null) {
    return null
  }

  return {
    datasetId: state.datasetId,
    episodeIndex: state.episodeIndex,
    globalTransform: state.globalTransform ?? undefined,
    cameraTransforms:
      Object.keys(state.cameraTransforms).length > 0 ? state.cameraTransforms : undefined,
    removedFrames:
      state.removedFrames.size > 0
        ? Array.from(state.removedFrames).sort((a, b) => a - b)
        : undefined,
    insertedFrames:
      state.insertedFrames.size > 0
        ? Array.from(state.insertedFrames.values()).sort(
            (a, b) => a.afterFrameIndex - b.afterFrameIndex,
          )
        : undefined,
    subtasks: state.subtasks.length > 0 ? state.subtasks : undefined,
    trajectoryAdjustments:
      state.trajectoryAdjustments.size > 0
        ? Array.from(state.trajectoryAdjustments.values())
        : undefined,
  }
}

export function hasEditContent(operations: EpisodeEditOperations) {
  return !!(
    operations.globalTransform ||
    operations.cameraTransforms ||
    operations.removedFrames ||
    operations.insertedFrames ||
    operations.subtasks ||
    operations.trajectoryAdjustments
  )
}

export function computeDirty(state: EditStateWithDerived): boolean {
  if (!state.originalState) return false

  if (
    JSON.stringify(state.globalTransform) !== JSON.stringify(state.originalState.globalTransform)
  ) {
    return true
  }

  if (
    JSON.stringify(state.cameraTransforms) !== JSON.stringify(state.originalState.cameraTransforms)
  ) {
    return true
  }

  if (state.removedFrames.size !== state.originalState.removedFrames.size) {
    return true
  }
  for (const frame of state.removedFrames) {
    if (!state.originalState.removedFrames.has(frame)) {
      return true
    }
  }

  if (state.insertedFrames.size !== state.originalState.insertedFrames.size) {
    return true
  }
  for (const [afterIdx, insertion] of state.insertedFrames) {
    const originalInsertion = state.originalState.insertedFrames.get(afterIdx)
    if (
      !originalInsertion ||
      insertion.interpolationFactor !== originalInsertion.interpolationFactor
    ) {
      return true
    }
  }

  if (JSON.stringify(state.subtasks) !== JSON.stringify(state.originalState.subtasks)) {
    return true
  }

  if (state.trajectoryAdjustments.size !== state.originalState.trajectoryAdjustments.size) {
    return true
  }
  for (const [frame, adjustment] of state.trajectoryAdjustments) {
    const originalAdjustment = state.originalState.trajectoryAdjustments.get(frame)
    if (!originalAdjustment || JSON.stringify(adjustment) !== JSON.stringify(originalAdjustment)) {
      return true
    }
  }

  return false
}

export function buildEditStateUpdate<T extends EditStateWithDerived>(
  state: T,
  updates: Partial<T>,
  options: EditStateUpdateOptions = {},
): T {
  const nextState = { ...state, ...updates }

  return {
    ...nextState,
    isDirty: computeDirty(nextState),
    validationErrors: options.validationErrors ?? nextState.validationErrors,
  }
}

export function buildDraftPersistencePayload(state: EditStateSnapshot) {
  const operations = buildEditOperations(state)
  const persistedDraft = operations && hasEditContent(operations) ? operations : null

  return {
    datasetId: state.datasetId,
    episodeIndex: state.episodeIndex,
    operations,
    persistedDraft,
  }
}

export async function persistEditStateDraft(state: EditStateSnapshot) {
  const { datasetId, episodeIndex, persistedDraft } = buildDraftPersistencePayload(state)

  if (!datasetId || episodeIndex === null) {
    return
  }

  await persistEditDraft(datasetId, episodeIndex, persistedDraft)
}
