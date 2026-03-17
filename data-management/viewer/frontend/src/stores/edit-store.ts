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
import {
  buildDraftPersistencePayload,
  buildEditOperations,
  buildEditStateUpdate,
  buildOriginalEditState,
  persistEditStateDraft,
} from '@/stores/edit-store-helpers'
import type {
  EpisodeEditOperations,
  FrameInsertion,
  ImageTransform,
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
  savedEpisodeDrafts: Record<string, EpisodeEditOperations>
}

interface EditActions {
  /** Initialize edit state for an episode */
  initializeEdit: (datasetId: string, episodeIndex: number) => void
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
  /** Commit the current episode edits as the saved draft baseline */
  saveEpisodeDraft: () => void
  /** Mark current state as saved */
  markSaved: () => void
  /** Reset to original state */
  resetEdits: () => void
  /** Clear all edit state */
  clear: () => void
}

type EditStore = EditState & EditActions

const initialState: EditState = {
  datasetId: null,
  episodeIndex: null,
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

function getEpisodeDraftKey(datasetId: string, episodeIndex: number) {
  return `${datasetId}:${episodeIndex}`
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
      const persistCurrentDraft = () => {
        void persistEditStateDraft(get())
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

        initializeEdit: (datasetId, episodeIndex) => {
          const draftKey = getEpisodeDraftKey(datasetId, episodeIndex)
          const savedDraft = get().savedEpisodeDrafts[draftKey]

          if (savedDraft) {
            get().loadEditOperations(savedDraft)
            return
          }

          const newState = {
            datasetId,
            episodeIndex,
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
              originalState: buildOriginalEditState(newState),
              isDirty: false,
              validationErrors: [],
            },
            false,
            'initializeEdit',
          )

          void loadPersistedEditDraft(datasetId, episodeIndex).then((persistedDraft) => {
            if (!persistedDraft) {
              return
            }

            const currentState = get()

            if (
              currentState.datasetId !== datasetId ||
              currentState.episodeIndex !== episodeIndex
            ) {
              return
            }

            set(
              (state) => ({
                savedEpisodeDrafts: {
                  ...state.savedEpisodeDrafts,
                  [draftKey]: persistedDraft,
                },
              }),
              false,
              'hydratePersistedEpisodeDraft',
            )

            get().loadEditOperations(persistedDraft)
          })
        },

        loadEditOperations: (ops) => {
          const removedSet = new Set(ops.removedFrames ?? [])
          const insertedMap = new Map<number, FrameInsertion>()
          for (const ins of ops.insertedFrames ?? []) {
            insertedMap.set(ins.afterFrameIndex, ins)
          }
          const subtasks = ops.subtasks ?? []
          const trajectoryAdjustments = new Map<number, TrajectoryAdjustment>()
          for (const adj of ops.trajectoryAdjustments ?? []) {
            trajectoryAdjustments.set(adj.frameIndex, adj)
          }

          set(
            {
              datasetId: ops.datasetId,
              episodeIndex: ops.episodeIndex,
              globalTransform: ops.globalTransform ?? null,
              cameraTransforms: ops.cameraTransforms ?? {},
              removedFrames: removedSet,
              insertedFrames: insertedMap,
              subtasks,
              trajectoryAdjustments,
              originalState: buildOriginalEditState({
                globalTransform: ops.globalTransform ?? null,
                cameraTransforms: ops.cameraTransforms ?? {},
                removedFrames: removedSet,
                insertedFrames: insertedMap,
                subtasks,
                trajectoryAdjustments,
              }),
              isDirty: false,
              validationErrors: validateSegments(subtasks),
            },
            false,
            'loadEditOperations',
          )

          void persistEditStateDraft({
            datasetId: ops.datasetId,
            episodeIndex: ops.episodeIndex,
            globalTransform: ops.globalTransform ?? null,
            cameraTransforms: ops.cameraTransforms ?? {},
            removedFrames: removedSet,
            insertedFrames: insertedMap,
            subtasks,
            trajectoryAdjustments,
          })
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
          const currentState = get()
          const { datasetId, episodeIndex, operations, persistedDraft } =
            buildDraftPersistencePayload(currentState)

          if (!operations || !datasetId || episodeIndex === null) {
            return
          }

          set(
            (state) => {
              const draftKey = getEpisodeDraftKey(datasetId, episodeIndex)
              const nextSavedEpisodeDrafts = { ...state.savedEpisodeDrafts }

              if (persistedDraft) {
                nextSavedEpisodeDrafts[draftKey] = operations
              } else {
                delete nextSavedEpisodeDrafts[draftKey]
              }

              return {
                savedEpisodeDrafts: nextSavedEpisodeDrafts,
                originalState: buildOriginalEditState(state),
                isDirty: false,
              }
            },
            false,
            'saveEpisodeDraft',
          )

          void persistEditStateDraft(currentState)
        },

        markSaved: () => {
          set(
            (state) => ({
              originalState: buildOriginalEditState(state),
              isDirty: false,
            }),
            false,
            'markSaved',
          )

          persistCurrentDraft()
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
          set(initialState, false, 'clear')
        },
      }
    },
    { name: 'edit-store' },
  ),
)
