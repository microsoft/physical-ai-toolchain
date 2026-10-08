/**
 * Edit store for managing episode editing state.
 *
 * Tracks non-destructive edit operations including:
 * - Image transforms (crop/resize)
 * - Frame removal
 * - Sub-task segmentation
 */

import { create } from 'zustand'
import { devtools } from 'zustand/middleware'

import { loadPersistedEditDraft } from '@/lib/edit-draft-storage'
import { recordDiagnosticEvent } from '@/lib/playback-diagnostics'
import {
  buildDraftPersistencePayload,
  buildEditOperations,
  buildEditStateFromOperations,
  buildEditStateUpdate,
  buildOriginalEditState,
  persistEditStateDraft,
} from '@/stores/edit-store-helpers'
import type {
  EpisodeEditDraft,
  EpisodeEditOperations,
  FrameInsertion,
  ImageTransform,
  SavedEditBaseline,
  SubtaskSegment,
  TrajectoryAdjustment,
} from '@/types/episode-edit'
import { validateSegments } from '@/types/episode-edit'

import {
  createEditStoreFrameActions,
  createEditStoreSubtaskActions,
  createEditStoreTransformActions,
} from './edit-store-action-factories'

export {
  getEffectiveFrameCount,
  getEffectiveIndex,
  getOriginalIndex,
} from './edit-store-frame-utils'

interface EditState {
  /** Current episode being edited */
  datasetId: string | null
  episodeIndex: number | null
  principalScopeId: string
  serverBaseline: SavedEditBaseline | null
  draftHydrated: boolean
  draftError: string | null

  /** Global transform applied to all cameras */
  globalTransform: ImageTransform | null
  /** Per-camera transform overrides */
  cameraTransforms: Record<string, ImageTransform>
  /** Set of frame indices marked for removal */
  removedFrames: Set<number>
  /** Map of inserted frames keyed by afterFrameIndex */
  insertedFrames: Map<number, FrameInsertion>
  /** Sub-task segments */
  subtasks: SubtaskSegment[]
  /** Trajectory adjustments per frame */
  trajectoryAdjustments: Map<number, TrajectoryAdjustment>

  /** Original state for dirty checking */
  originalState: {
    globalTransform: ImageTransform | null
    cameraTransforms: Record<string, ImageTransform>
    removedFrames: Set<number>
    insertedFrames: Map<number, FrameInsertion>
    subtasks: SubtaskSegment[]
    trajectoryAdjustments: Map<number, TrajectoryAdjustment>
  } | null

  /** Whether there are unsaved changes */
  isDirty: boolean
  /** Validation errors */
  validationErrors: string[]
  /** Saved draft operations keyed by dataset and episode */
  savedEpisodeDrafts: Record<string, EpisodeEditDraft>
}

interface EditActions {
  /** Initialize edit state for an episode */
  initializeEdit: (datasetId: string, episodeIndex: number, principalScopeId?: string) => void
  /** Load existing edit operations */
  loadEditOperations: (ops: EpisodeEditOperations) => void

  // Transform actions
  /** Set the global transform */
  setGlobalTransform: (transform: ImageTransform | null) => void
  /** Set a camera-specific transform */
  setCameraTransform: (camera: string, transform: ImageTransform | null) => void
  /** Clear all transforms */
  clearTransforms: () => void

  // Frame removal actions
  /** Toggle frame removal status */
  toggleFrameRemoval: (frameIndex: number) => void
  /** Add a range of frames to removal */
  addFrameRange: (start: number, end: number) => void
  /** Add frames at a configurable frequency (every Nth frame) */
  addFramesByFrequency: (start: number, end: number, frequency: number) => void
  /** Remove a range of frames from removal */
  removeFrameRange: (start: number, end: number) => void
  /** Clear all removed frames */
  clearRemovedFrames: () => void

  // Frame insertion actions
  /** Insert a frame after the specified index */
  insertFrame: (afterFrameIndex: number, factor?: number) => void
  /** Remove an inserted frame */
  removeInsertedFrame: (afterFrameIndex: number) => void
  /** Clear all inserted frames */
  clearInsertedFrames: () => void

  // Subtask actions
  /** Add a new subtask segment */
  addSubtask: (segment: SubtaskSegment) => void
  /** Add a subtask from frame range */
  addSubtaskFromRange: (start: number, end: number) => void
  /** Update a subtask segment */
  updateSubtask: (id: string, update: Partial<SubtaskSegment>) => void
  /** Remove a subtask segment */
  removeSubtask: (id: string) => void
  /** Reorder subtasks */
  reorderSubtasks: (fromIndex: number, toIndex: number) => void

  // Trajectory adjustment actions
  /** Set a trajectory adjustment for a specific frame */
  setTrajectoryAdjustment: (
    frameIndex: number,
    adjustment: Omit<TrajectoryAdjustment, 'frameIndex'>,
  ) => void
  /** Remove a trajectory adjustment for a specific frame */
  removeTrajectoryAdjustment: (frameIndex: number) => void
  /** Get trajectory adjustment for a specific frame */
  getTrajectoryAdjustment: (frameIndex: number) => TrajectoryAdjustment | undefined
  /** Clear all trajectory adjustments */
  clearTrajectoryAdjustments: () => void

  // State management
  /** Get the current edit operations for export */
  getEditOperations: () => EpisodeEditOperations | null
  saveEpisodeDraft: () => void
  hydrateSavedEdits: (baseline: SavedEditBaseline) => boolean
  acknowledgeSave: (submitted: SavedEditBaseline, saved: SavedEditBaseline) => void
  /** Reset to original state */
  resetEdits: () => void
  /** Clear all edit state */
  clear: () => void
}

type EditStore = EditState & EditActions

const initialState: EditState = {
  datasetId: null,
  episodeIndex: null,
  principalScopeId: 'local',
  serverBaseline: null,
  draftHydrated: false,
  draftError: null,
  globalTransform: null,
  cameraTransforms: {},
  removedFrames: new Set(),
  insertedFrames: new Map(),
  subtasks: [],
  trajectoryAdjustments: new Map(),
  originalState: null,
  isDirty: false,
  validationErrors: [],
  savedEpisodeDrafts: {},
}

function getEpisodeDraftKey(datasetId: string, episodeIndex: number, principalScopeId: string) {
  return JSON.stringify([principalScopeId, datasetId, episodeIndex])
}

function sameEditRevision(first: SavedEditBaseline, second: SavedEditBaseline): boolean {
  return (
    first.sourceId === second.sourceId &&
    first.sourceRevision === second.sourceRevision &&
    first.principalScopeId === second.principalScopeId &&
    first.etag === second.etag &&
    first.operations.datasetId === second.operations.datasetId &&
    first.operations.episodeIndex === second.operations.episodeIndex
  )
}

/**
 * Zustand store for episode edit state management.
 *
 * @example
 * ```tsx
 * const {
 *   globalTransform,
 *   setGlobalTransform,
 *   removedFrames,
 *   toggleFrameRemoval,
 *   subtasks,
 *   addSubtaskFromRange,
 * } = useEditStore();
 *
 * // Set a crop transform
 * setGlobalTransform({ crop: { x: 10, y: 10, width: 200, height: 150 } });
 *
 * // Mark a frame for removal
 * toggleFrameRemoval(42);
 *
 * // Add a subtask segment
 * addSubtaskFromRange(100, 200);
 * ```
 */
export const useEditStore = create<EditStore>()(
  devtools(
    (set, get) => {
      let sessionGeneration = 0
      const persistCurrentDraft = () => {
        const current = get()
        const { datasetId, episodeIndex, persistedDraft } = buildDraftPersistencePayload(current)
        if (!datasetId || episodeIndex === null) return
        const key = getEpisodeDraftKey(datasetId, episodeIndex, current.principalScopeId)
        const drafts = { ...current.savedEpisodeDrafts }
        if (persistedDraft) {
          drafts[key] = {
            operations: structuredClone(persistedDraft),
            baseline: structuredClone(current.serverBaseline),
          }
        } else {
          delete drafts[key]
        }
        set({ savedEpisodeDrafts: drafts }, false, 'retainCurrentEditDraft')
        const generation = sessionGeneration
        void persistEditStateDraft(current).catch(() => {
          recordDiagnosticEvent('persistence', 'edit-draft-write-failed', {
            reason: 'storage-unavailable',
          })
          if (generation === sessionGeneration)
            set({ draftError: 'Browser draft could not be stored.' }, false, 'editDraftWriteFailed')
        })
      }

      const updateState = (
        actionName: string,
        recipe: (state: EditStore) => Partial<EditStore>,
        options?: { validationErrors?: (nextState: EditStore) => string[] },
      ) => {
        set(
          (state) => {
            const updates = recipe(state)
            const nextState = { ...state, ...updates } as EditStore

            return buildEditStateUpdate(state, updates, {
              validationErrors: options?.validationErrors?.(nextState),
            })
          },
          false,
          actionName,
        )

        persistCurrentDraft()
      }

      const transformActions = createEditStoreTransformActions<EditStore>(updateState)
      const frameActions = createEditStoreFrameActions<EditStore>(updateState)
      const subtaskActions = createEditStoreSubtaskActions<EditStore>(updateState, get)

      return {
        ...initialState,

        initializeEdit: (datasetId, episodeIndex, principalScopeId = 'local') => {
          const generation = ++sessionGeneration
          const draftKey = getEpisodeDraftKey(datasetId, episodeIndex, principalScopeId)
          const savedDraft = get().savedEpisodeDrafts[draftKey]

          const newState = {
            datasetId,
            episodeIndex,
            principalScopeId,
            serverBaseline: savedDraft?.baseline ?? null,
            draftHydrated: !!savedDraft,
            draftError: null,
            globalTransform: null,
            cameraTransforms: {},
            removedFrames: new Set<number>(),
            insertedFrames: new Map<number, FrameInsertion>(),
            subtasks: [],
            trajectoryAdjustments: new Map<number, TrajectoryAdjustment>(),
          }

          set(
            {
              ...newState,
              originalState: savedDraft?.baseline
                ? buildEditStateFromOperations(savedDraft.baseline.operations)
                : buildOriginalEditState(newState),
              isDirty: false,
              validationErrors: [],
            },
            false,
            'initializeEdit',
          )

          if (savedDraft) {
            get().loadEditOperations(savedDraft.operations)
            return
          }

          const initializedState = get()
          void loadPersistedEditDraft(datasetId, episodeIndex, principalScopeId)
            .then((persistedDraft) => {
              if (generation !== sessionGeneration) return
              const currentState = get()

              if (
                currentState !== initializedState ||
                currentState.datasetId !== datasetId ||
                currentState.episodeIndex !== episodeIndex ||
                currentState.principalScopeId !== principalScopeId
              ) {
                recordDiagnosticEvent('persistence', 'edit-draft-hydration-skipped', {
                  reason: 'state-changed',
                })
                set({ draftHydrated: true }, false, 'finishEditDraftRecovery')
                return
              }

              if (!persistedDraft) {
                set({ draftHydrated: true }, false, 'finishEditDraftRecovery')
                return
              }

              set(
                (state) => ({
                  draftHydrated: true,
                  serverBaseline: persistedDraft.baseline,
                  originalState: persistedDraft.baseline
                    ? buildEditStateFromOperations(persistedDraft.baseline.operations)
                    : state.originalState,
                  savedEpisodeDrafts: {
                    ...state.savedEpisodeDrafts,
                    [draftKey]: {
                      operations: persistedDraft.draft,
                      baseline: persistedDraft.baseline,
                    },
                  },
                }),
                false,
                'hydratePersistedEpisodeDraft',
              )

              get().loadEditOperations(persistedDraft.draft)
            })
            .catch(() => {
              recordDiagnosticEvent('persistence', 'edit-draft-read-failed', {
                reason: 'storage-unavailable',
              })
              if (generation === sessionGeneration)
                set(
                  { draftHydrated: true, draftError: 'Browser draft could not be recovered.' },
                  false,
                  'editDraftReadFailed',
                )
            })
        },

        loadEditOperations: (ops) => {
          updateState(
            'loadEditOperations',
            () => ({
              datasetId: ops.datasetId,
              episodeIndex: ops.episodeIndex,
              ...buildEditStateFromOperations(ops),
            }),
            { validationErrors: (state) => validateSegments(state.subtasks) },
          )
        },

        ...transformActions,

        ...frameActions,

        ...subtaskActions,

        getTrajectoryAdjustment: (frameIndex) => {
          return get().trajectoryAdjustments.get(frameIndex)
        },

        getEditOperations: () => {
          return buildEditOperations(get())
        },

        saveEpisodeDraft: () => {
          persistCurrentDraft()
        },

        hydrateSavedEdits: (baseline) => {
          const current = get()
          if (
            current.datasetId !== baseline.operations.datasetId ||
            current.episodeIndex !== baseline.operations.episodeIndex ||
            current.principalScopeId !== baseline.principalScopeId ||
            !current.draftHydrated ||
            current.draftError ||
            current.isDirty ||
            (current.serverBaseline && !sameEditRevision(current.serverBaseline, baseline))
          ) {
            recordDiagnosticEvent('persistence', 'edit-server-hydration-skipped', {
              reason: 'scope-or-draft-changed',
            })
            return false
          }
          const snapshot = structuredClone(baseline)
          const operations = buildEditStateFromOperations(snapshot.operations)
          set(
            {
              ...operations,
              serverBaseline: snapshot,
              originalState: buildOriginalEditState(operations),
              isDirty: false,
              validationErrors: validateSegments(operations.subtasks),
            },
            false,
            'hydrateSavedEdits',
          )
          return true
        },

        acknowledgeSave: (submitted, saved) => {
          const current = get()
          if (
            !current.serverBaseline ||
            !sameEditRevision(current.serverBaseline, submitted) ||
            !sameEditRevision(submitted, { ...saved, etag: submitted.etag }) ||
            !saved.etag ||
            current.datasetId !== submitted.operations.datasetId ||
            current.episodeIndex !== submitted.operations.episodeIndex ||
            current.principalScopeId !== submitted.principalScopeId
          ) {
            recordDiagnosticEvent('persistence', 'edit-save-acknowledgment-skipped', {
              reason: 'scope-or-revision-changed',
            })
            return
          }
          updateState('acknowledgeSave', () => ({
            ...(JSON.stringify(buildEditOperations(current)) ===
            JSON.stringify(submitted.operations)
              ? buildEditStateFromOperations(saved.operations)
              : {}),
            serverBaseline: structuredClone(saved),
            originalState: buildEditStateFromOperations(saved.operations),
          }))
        },

        resetEdits: () => {
          set(
            (state) => {
              if (!state.originalState) {
                return state
              }

              return {
                ...state,
                globalTransform: state.originalState.globalTransform,
                cameraTransforms: structuredClone(state.originalState.cameraTransforms),
                removedFrames: new Set(state.originalState.removedFrames),
                insertedFrames: new Map(state.originalState.insertedFrames),
                subtasks: structuredClone(state.originalState.subtasks),
                trajectoryAdjustments: new Map(state.originalState.trajectoryAdjustments),
                isDirty: false,
                validationErrors: validateSegments(state.originalState.subtasks),
              }
            },
            false,
            'resetEdits',
          )

          persistCurrentDraft()
        },

        clear: () => {
          sessionGeneration += 1
          set(initialState, false, 'clear')
        },
      }
    },
    { name: 'edit-store' },
  ),
)
