/**
 * Annotation store for managing annotation editing state.
 */

import { create } from 'zustand'
import { devtools } from 'zustand/middleware'

import type {
  Anomaly,
  DataQualityAnnotation,
  EpisodeAnnotation,
  LanguageInstructionAnnotation,
  TaskCompletenessAnnotation,
  TrajectoryQualityAnnotation,
} from '@/types'

interface AnnotationState {
  /** Current annotation being edited */
  currentAnnotation: EpisodeAnnotation | null
  /** Original annotation (for dirty checking) */
  originalAnnotation: EpisodeAnnotation | null
  /** Whether the annotation has unsaved changes */
  isDirty: boolean
  /** Whether a save operation is in progress */
  isSaving: boolean
  /** Error message if any */
  error: string | null
  /** Current annotator ID */
  annotatorId: string
}

interface AnnotationActions {
  /** Initialize a new annotation for the current user */
  initializeAnnotation: (annotatorId: string) => void
  /** Load an existing annotation */
  loadAnnotation: (annotation: EpisodeAnnotation) => void
  /** Update task completeness annotation */
  updateTaskCompleteness: (update: Partial<TaskCompletenessAnnotation>) => void
  /** Update trajectory quality annotation */
  updateTrajectoryQuality: (update: Partial<TrajectoryQualityAnnotation>) => void
  /** Update data quality annotation */
  updateDataQuality: (update: Partial<DataQualityAnnotation>) => void
  /** Add a new anomaly */
  addAnomaly: (anomaly: Anomaly) => void
  /** Update an existing anomaly */
  updateAnomaly: (id: string, update: Partial<Anomaly>) => void
  /** Remove an anomaly by ID */
  removeAnomaly: (id: string) => void
  /** Toggle anomaly verification status */
  toggleAnomalyVerified: (id: string) => void
  /** Update notes */
  updateNotes: (notes: string) => void
  /** Update language instruction annotation */
  updateLanguageInstruction: (update: Partial<LanguageInstructionAnnotation>) => void
  /** Clear language instruction */
  clearLanguageInstruction: () => void
  /** Set saving state */
  setSaving: (isSaving: boolean) => void
  /** Set error state */
  setError: (error: string | null) => void
  /** Mark annotation as saved (reset dirty state) */
  markSaved: () => void
  /** Reset annotation to original state */
  resetAnnotation: () => void
  /** Clear the current annotation */
  clear: () => void
}

type AnnotationStore = AnnotationState & AnnotationActions

const initialState: AnnotationState = {
  currentAnnotation: null,
  originalAnnotation: null,
  isDirty: false,
  isSaving: false,
  error: null,
  annotatorId: '',
}

/**
 * Zustand store for annotation editing state.
 *
 * @example
 * ```tsx
 * const { currentAnnotation, updateTaskCompleteness, isDirty } = useAnnotationStore();
 *
 * // Update task completeness rating
 * updateTaskCompleteness({ rating: 'success', confidence: 4 });
 *
 * // Check if there are unsaved changes
 * if (isDirty) {
 *   console.log('Unsaved changes!');
 * }
 * ```
 */
export const useAnnotationStore = create<AnnotationStore>()(
  devtools(
    (set, get) => ({
      ...initialState,

      initializeAnnotation: (annotatorId) => {
        const newAnnotation: EpisodeAnnotation = {
          annotatorId,
          timestamp: new Date().toISOString(),
          taskCompleteness: {
            rating: 'unknown',
            confidence: 3,
          },
          trajectoryQuality: {
            overallScore: 3,
            metrics: {
              smoothness: 3,
              efficiency: 3,
              safety: 3,
              precision: 3,
            },
            flags: [],
          },
          dataQuality: {
            overallQuality: 'good',
            issues: [],
          },
          anomalies: {
            anomalies: [],
          },
        }

        set(
          {
            currentAnnotation: newAnnotation,
            originalAnnotation: structuredClone(newAnnotation),
            annotatorId,
            isDirty: false,
            error: null,
          },
          false,
          'initializeAnnotation',
        )
      },

      loadAnnotation: (annotation) => {
        set(
          {
            currentAnnotation: structuredClone(annotation),
            originalAnnotation: structuredClone(annotation),
            annotatorId: annotation.annotatorId,
            isDirty: false,
            error: null,
          },
          false,
          'loadAnnotation',
        )
      },

      updateTaskCompleteness: (update) => {
        const { currentAnnotation } = get()
        if (!currentAnnotation) return

        set(
          {
            currentAnnotation: {
              ...currentAnnotation,
              timestamp: new Date().toISOString(),
              taskCompleteness: {
                ...currentAnnotation.taskCompleteness,
                ...update,
              },
            },
            isDirty: true,
          },
          false,
          'updateTaskCompleteness',
        )
      },

      updateTrajectoryQuality: (update) => {
        const { currentAnnotation } = get()
        if (!currentAnnotation) return

        set(
          {
            currentAnnotation: {
              ...currentAnnotation,
              timestamp: new Date().toISOString(),
              trajectoryQuality: {
                ...currentAnnotation.trajectoryQuality,
                ...update,
                metrics: {
                  ...currentAnnotation.trajectoryQuality.metrics,
                  ...(update.metrics ?? {}),
                },
              },
            },
            isDirty: true,
          },
          false,
          'updateTrajectoryQuality',
        )
      },

      updateDataQuality: (update) => {
        const { currentAnnotation } = get()
        if (!currentAnnotation) return

        set(
          {
            currentAnnotation: {
              ...currentAnnotation,
              timestamp: new Date().toISOString(),
              dataQuality: {
                ...currentAnnotation.dataQuality,
                ...update,
              },
            },
            isDirty: true,
          },
          false,
          'updateDataQuality',
        )
      },

      addAnomaly: (anomaly) => {
        const { currentAnnotation } = get()
        if (!currentAnnotation) return

        set(
          {
            currentAnnotation: {
              ...currentAnnotation,
              timestamp: new Date().toISOString(),
              anomalies: {
                anomalies: [...currentAnnotation.anomalies.anomalies, anomaly],
              },
            },
            isDirty: true,
          },
          false,
          'addAnomaly',
        )
      },

      updateAnomaly: (id, update) => {
        const { currentAnnotation } = get()
        if (!currentAnnotation) return

        set(
          {
            currentAnnotation: {
              ...currentAnnotation,
              timestamp: new Date().toISOString(),
              anomalies: {
                anomalies: currentAnnotation.anomalies.anomalies.map((a) =>
                  a.id === id ? { ...a, ...update } : a,
                ),
              },
            },
            isDirty: true,
          },
          false,
          'updateAnomaly',
        )
      },

      removeAnomaly: (id) => {
        const { currentAnnotation } = get()
        if (!currentAnnotation) return

        set(
          {
            currentAnnotation: {
              ...currentAnnotation,
              timestamp: new Date().toISOString(),
              anomalies: {
                anomalies: currentAnnotation.anomalies.anomalies.filter((a) => a.id !== id),
              },
            },
            isDirty: true,
          },
          false,
          'removeAnomaly',
        )
      },

      toggleAnomalyVerified: (id) => {
        const { currentAnnotation } = get()
        if (!currentAnnotation) return

        set(
          {
            currentAnnotation: {
              ...currentAnnotation,
              timestamp: new Date().toISOString(),
              anomalies: {
                anomalies: currentAnnotation.anomalies.anomalies.map((a) =>
                  a.id === id ? { ...a, verified: !a.verified } : a,
                ),
              },
            },
            isDirty: true,
          },
          false,
          'toggleAnomalyVerified',
        )
      },

      updateNotes: (notes) => {
        const { currentAnnotation } = get()
        if (!currentAnnotation) return

        set(
          {
            currentAnnotation: {
              ...currentAnnotation,
              timestamp: new Date().toISOString(),
              notes,
            },
            isDirty: true,
          },
          false,
          'updateNotes',
        )
      },

      updateLanguageInstruction: (update) => {
        const { currentAnnotation } = get()
        if (!currentAnnotation) return

        const existing = currentAnnotation.languageInstruction ?? {
          instruction: '',
          source: 'human' as const,
          language: 'en',
          paraphrases: [],
          subtaskInstructions: [],
        }

        set(
          {
            currentAnnotation: {
              ...currentAnnotation,
              timestamp: new Date().toISOString(),
              languageInstruction: {
                ...existing,
                ...update,
              },
            },
            isDirty: true,
          },
          false,
          'updateLanguageInstruction',
        )
      },

      clearLanguageInstruction: () => {
        const { currentAnnotation } = get()
        if (!currentAnnotation) return

        const { languageInstruction: _, ...rest } = currentAnnotation
        set(
          {
            currentAnnotation: {
              ...rest,
              timestamp: new Date().toISOString(),
            },
            isDirty: true,
          },
          false,
          'clearLanguageInstruction',
        )
      },

      setSaving: (isSaving) => {
        set({ isSaving }, false, 'setSaving')
      },

      setError: (error) => {
        set({ error, isSaving: false }, false, 'setError')
      },

      markSaved: () => {
        const { currentAnnotation } = get()
        set(
          {
            originalAnnotation: currentAnnotation ? structuredClone(currentAnnotation) : null,
            isDirty: false,
            isSaving: false,
          },
          false,
          'markSaved',
        )
      },

      resetAnnotation: () => {
        const { originalAnnotation } = get()
        set(
          {
            currentAnnotation: originalAnnotation ? structuredClone(originalAnnotation) : null,
            isDirty: false,
            error: null,
          },
          false,
          'resetAnnotation',
        )
      },

      clear: () => {
        set(initialState, false, 'clear')
      },
    }),
    { name: 'annotation-store' },
  ),
)

// Selector hooks for common patterns
export const useAnnotationDirtyState = () =>
  useAnnotationStore((state) => ({
    isDirty: state.isDirty,
    isSaving: state.isSaving,
  }))

export const useTaskCompletenessState = () =>
  useAnnotationStore((state) => ({
    taskCompleteness: state.currentAnnotation?.taskCompleteness,
    updateTaskCompleteness: state.updateTaskCompleteness,
  }))

export const useTrajectoryQualityState = () =>
  useAnnotationStore((state) => ({
    trajectoryQuality: state.currentAnnotation?.trajectoryQuality,
    updateTrajectoryQuality: state.updateTrajectoryQuality,
  }))

export const useDataQualityState = () =>
  useAnnotationStore((state) => ({
    dataQuality: state.currentAnnotation?.dataQuality,
    updateDataQuality: state.updateDataQuality,
  }))

export const useAnomalyState = () =>
  useAnnotationStore((state) => ({
    anomalies: state.currentAnnotation?.anomalies.anomalies ?? [],
    addAnomaly: state.addAnomaly,
    updateAnomaly: state.updateAnomaly,
    removeAnomaly: state.removeAnomaly,
    toggleAnomalyVerified: state.toggleAnomalyVerified,
  }))
