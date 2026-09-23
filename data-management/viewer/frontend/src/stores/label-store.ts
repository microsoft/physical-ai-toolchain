/**
 * Label store for managing episode labels and available label options.
 */

import { create } from 'zustand'
import { devtools } from 'zustand/middleware'

import type { EpisodeAnalysisRecord } from '@/types/api'

interface LabelState {
  /** Dataset whose labels currently hydrate this store */
  datasetId: string | null
  /** Available label options for the current dataset */
  availableLabels: string[]
  /** Labels per episode: episode index -> label list */
  episodeLabels: Record<number, string[]>
  /** Saved labels per episode from the last persisted dataset state */
  savedEpisodeLabels: Record<number, string[]>
  /** Structured analysis record per episode (VLM labels + motion metrics) */
  episodeAnalysis: Record<number, EpisodeAnalysisRecord>
  /** Whether the label data has been loaded */
  isLoaded: boolean
  /** Label filter: only show episodes with these labels (empty = show all) */
  filterLabels: string[]
  /** Generation of local label edits in the active dataset */
  editGeneration: number
  /** Stale-write conflict retained for explicit resolution */
  conflict: {
    currentEtag: string | null
    episodeIndex: number
    submittedLabels: string[]
  } | null
}

interface LabelActions {
  /** Reset dataset-scoped state when the selected dataset changes */
  prepareDatasetLabels: (datasetId: string | null) => void
  /** Set available label options */
  setAvailableLabels: (labels: string[]) => void
  /** Add a new label option */
  addLabelOption: (label: string) => void
  /** Remove a label option and strip it from assignments */
  removeLabelOption: (label: string) => void
  /** Set all episode labels at once (bulk load) */
  setAllEpisodeLabels: (episodes: Record<string, string[]>) => void
  /** Replace labels when loading a selected dataset */
  setDatasetEpisodeLabels: (datasetId: string, episodes: Record<string, string[]>) => void
  /** Reconcile server labels while preserving unsaved local edits */
  reconcileEpisodeLabels: (datasetId: string, episodes: Record<string, string[]>) => void
  /** Restore a dirty local label draft over its server baseline */
  restoreLabelDraft: (
    availableLabels: string[],
    episodeLabels: Record<number, string[]>,
    savedEpisodeLabels: Record<number, string[]>,
  ) => void
  /** Set all episode analysis records at once (bulk load) */
  setAllEpisodeAnalysis: (analysis: Record<string, EpisodeAnalysisRecord>) => void
  /** Set labels for a specific episode */
  setEpisodeLabels: (episodeIndex: number, labels: string[]) => void
  /** Commit the saved baseline for a specific episode */
  commitEpisodeLabels: (episodeIndex: number, labels?: string[]) => void
  /** Commit a submitted baseline without replacing later local edits */
  commitSubmittedEpisodeLabels: (
    episodeIndex: number,
    submittedLabels: string[],
    savedLabels: string[],
  ) => void
  /** Retain a stale-write conflict without discarding local edits */
  setConflict: (currentEtag: string | null, episodeIndex: number, submittedLabels: string[]) => void
  /** Apply rebased labels over the latest server baseline */
  resolveConflict: (
    availableLabels: string[],
    mergedEpisodeLabels: Record<number, string[]>,
    serverEpisodeLabels: Record<number, string[]>,
  ) => void
  /** Toggle a label on/off for an episode */
  toggleLabel: (episodeIndex: number, label: string) => void
  /** Set filter labels */
  setFilterLabels: (labels: string[]) => void
  /** Toggle a filter label */
  toggleFilterLabel: (label: string) => void
  /** Mark loaded */
  setLoaded: (loaded: boolean) => void
  /** Reset store */
  reset: () => void
}

type LabelStore = LabelState & LabelActions

export const DEFAULT_LABELS: string[] = ['SUCCESS', 'FAILURE', 'PARTIAL']

const initialState: LabelState = {
  datasetId: null,
  availableLabels: DEFAULT_LABELS,
  episodeLabels: {},
  savedEpisodeLabels: {},
  episodeAnalysis: {},
  isLoaded: false,
  filterLabels: [],
  editGeneration: 0,
  conflict: null,
}

export const useLabelStore = create<LabelStore>()(
  devtools(
    (set, get) => ({
      ...initialState,

      prepareDatasetLabels: (datasetId) => {
        if (get().datasetId === datasetId) return
        set(
          {
            datasetId,
            availableLabels: DEFAULT_LABELS,
            episodeLabels: {},
            savedEpisodeLabels: {},
            episodeAnalysis: {},
            isLoaded: false,
            filterLabels: [],
            editGeneration: 0,
            conflict: null,
          },
          false,
          'prepareDatasetLabels',
        )
      },

      setAvailableLabels: (labels) => {
        const allowed = new Set(labels)
        set(
          (state) => ({
            availableLabels: labels,
            filterLabels: state.filterLabels.filter((label) => allowed.has(label)),
          }),
          false,
          'setAvailableLabels',
        )
      },

      addLabelOption: (label) => {
        const normalized = label.trim().toUpperCase()
        if (!normalized) return
        const { availableLabels } = get()
        if (!availableLabels.includes(normalized)) {
          set({ availableLabels: [...availableLabels, normalized] }, false, 'addLabelOption')
        }
      },

      removeLabelOption: (label) => {
        const normalized = label.trim().toUpperCase()
        if (!normalized) return

        const { availableLabels, episodeLabels, savedEpisodeLabels, filterLabels } = get()
        const nextEpisodeLabels = Object.fromEntries(
          Object.entries(episodeLabels).map(([episodeIndex, labels]) => [
            episodeIndex,
            labels.filter((existing) => existing !== normalized),
          ]),
        ) as Record<number, string[]>
        const nextSavedEpisodeLabels = Object.fromEntries(
          Object.entries(savedEpisodeLabels).map(([episodeIndex, labels]) => [
            episodeIndex,
            labels.filter((existing) => existing !== normalized),
          ]),
        ) as Record<number, string[]>

        set(
          {
            availableLabels: availableLabels.filter((existing) => existing !== normalized),
            episodeLabels: nextEpisodeLabels,
            savedEpisodeLabels: nextSavedEpisodeLabels,
            filterLabels: filterLabels.filter((existing) => existing !== normalized),
          },
          false,
          'removeLabelOption',
        )
      },

      setAllEpisodeLabels: (episodes) => {
        const parsed: Record<number, string[]> = {}
        for (const [key, labels] of Object.entries(episodes)) {
          parsed[Number(key)] = labels
        }
        set(
          { episodeLabels: parsed, savedEpisodeLabels: parsed, editGeneration: 0, conflict: null },
          false,
          'setAllEpisodeLabels',
        )
      },

      setDatasetEpisodeLabels: (datasetId, episodes) => {
        const parsed: Record<number, string[]> = {}
        for (const [key, labels] of Object.entries(episodes)) {
          parsed[Number(key)] = labels
        }
        set(
          {
            datasetId,
            episodeLabels: parsed,
            savedEpisodeLabels: parsed,
            filterLabels: get().datasetId === datasetId ? get().filterLabels : [],
            editGeneration: 0,
            conflict: null,
          },
          false,
          'setDatasetEpisodeLabels',
        )
      },

      reconcileEpisodeLabels: (datasetId, episodes) => {
        const { datasetId: currentDatasetId, episodeLabels, savedEpisodeLabels } = get()
        if (currentDatasetId !== datasetId) return

        const parsed: Record<number, string[]> = {}
        for (const [key, labels] of Object.entries(episodes)) {
          parsed[Number(key)] = labels
        }

        const reconciled = { ...parsed }
        for (const [key, localLabels] of Object.entries(episodeLabels)) {
          const episodeIndex = Number(key)
          const previousLabels = savedEpisodeLabels[episodeIndex] ?? []
          const incomingLabels = parsed[episodeIndex] ?? []
          const localSet = new Set(localLabels)
          const previousSet = new Set(previousLabels)
          const removedLocally = new Set(previousLabels.filter((label) => !localSet.has(label)))
          const addedLocally = localLabels.filter((label) => !previousSet.has(label))

          reconciled[episodeIndex] = [
            ...incomingLabels.filter((label) => !removedLocally.has(label)),
            ...addedLocally.filter((label) => !incomingLabels.includes(label)),
          ]
        }

        set(
          { episodeLabels: reconciled, savedEpisodeLabels: parsed },
          false,
          'reconcileEpisodeLabels',
        )
      },

      restoreLabelDraft: (availableLabels, episodeLabels, savedEpisodeLabels) => {
        set(
          {
            availableLabels,
            episodeLabels,
            savedEpisodeLabels,
            editGeneration: 1,
            conflict: null,
          },
          false,
          'restoreLabelDraft',
        )
      },
      setAllEpisodeAnalysis: (analysis) => {
        const parsed: Record<number, EpisodeAnalysisRecord> = {}
        for (const [key, record] of Object.entries(analysis)) {
          parsed[Number(key)] = record
        }
        set({ episodeAnalysis: parsed }, false, 'setAllEpisodeAnalysis')
      },

      setEpisodeLabels: (episodeIndex, labels) => {
        const { episodeLabels } = get()
        set(
          {
            episodeLabels: { ...episodeLabels, [episodeIndex]: labels },
            editGeneration: get().editGeneration + 1,
          },
          false,
          'setEpisodeLabels',
        )
      },

      commitEpisodeLabels: (episodeIndex, labels) => {
        const { episodeLabels, savedEpisodeLabels } = get()
        const nextLabels = labels ?? episodeLabels[episodeIndex] ?? []

        set(
          {
            episodeLabels: { ...episodeLabels, [episodeIndex]: nextLabels },
            savedEpisodeLabels: { ...savedEpisodeLabels, [episodeIndex]: nextLabels },
          },
          false,
          'commitEpisodeLabels',
        )
      },

      commitSubmittedEpisodeLabels: (episodeIndex, submittedLabels, savedLabels) => {
        const { episodeLabels, savedEpisodeLabels } = get()
        const currentLabels = episodeLabels[episodeIndex]
        const unchangedSinceSubmit =
          currentLabels === undefined ||
          (currentLabels.length === submittedLabels.length &&
            currentLabels.every((label, index) => label === submittedLabels[index]))
        set(
          {
            episodeLabels: unchangedSinceSubmit
              ? { ...episodeLabels, [episodeIndex]: savedLabels }
              : episodeLabels,
            savedEpisodeLabels: { ...savedEpisodeLabels, [episodeIndex]: savedLabels },
            conflict: null,
          },
          false,
          'commitSubmittedEpisodeLabels',
        )
      },

      setConflict: (currentEtag, episodeIndex, submittedLabels) => {
        set(
          {
            conflict: {
              currentEtag,
              episodeIndex,
              submittedLabels: structuredClone(submittedLabels),
            },
          },
          false,
          'setLabelConflict',
        )
      },

      resolveConflict: (availableLabels, mergedEpisodeLabels, serverEpisodeLabels) => {
        set(
          {
            availableLabels,
            episodeLabels: mergedEpisodeLabels,
            savedEpisodeLabels: serverEpisodeLabels,
            conflict: null,
            editGeneration: get().editGeneration + 1,
          },
          false,
          'resolveLabelConflict',
        )
      },

      toggleLabel: (episodeIndex, label) => {
        const { episodeLabels } = get()
        const current = episodeLabels[episodeIndex] || []
        const updated = current.includes(label)
          ? current.filter((l) => l !== label)
          : [...current, label]
        set(
          {
            episodeLabels: { ...episodeLabels, [episodeIndex]: updated },
            editGeneration: get().editGeneration + 1,
          },
          false,
          'toggleLabel',
        )
      },

      setFilterLabels: (labels) => {
        set({ filterLabels: labels }, false, 'setFilterLabels')
      },

      toggleFilterLabel: (label) => {
        const { filterLabels } = get()
        const updated = filterLabels.includes(label)
          ? filterLabels.filter((l) => l !== label)
          : [...filterLabels, label]
        set({ filterLabels: updated }, false, 'toggleFilterLabel')
      },

      setLoaded: (loaded) => {
        set({ isLoaded: loaded }, false, 'setLoaded')
      },

      reset: () => {
        set(initialState, false, 'reset')
      },
    }),
    { name: 'label-store' },
  ),
)
