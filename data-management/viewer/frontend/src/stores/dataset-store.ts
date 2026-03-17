/**
 * Dataset store for managing dataset selection and list state.
 */

import { create } from 'zustand'
import { devtools } from 'zustand/middleware'

import type { DatasetInfo } from '@/types'

interface DatasetState {
  /** List of available datasets */
  datasets: DatasetInfo[]
  /** Currently selected dataset */
  currentDataset: DatasetInfo | null
  /** Loading state */
  isLoading: boolean
  /** Error message if any */
  error: string | null
}

interface DatasetActions {
  /** Set the list of datasets */
  setDatasets: (datasets: DatasetInfo[]) => void
  /** Select a dataset by ID */
  selectDataset: (id: string) => void
  /** Clear the current dataset selection */
  clearSelection: () => void
  /** Set loading state */
  setLoading: (isLoading: boolean) => void
  /** Set error state */
  setError: (error: string | null) => void
  /** Reset the store to initial state */
  reset: () => void
}

type DatasetStore = DatasetState & DatasetActions

const initialState: DatasetState = {
  datasets: [],
  currentDataset: null,
  isLoading: false,
  error: null,
}

/**
 * Zustand store for dataset state management.
 *
 * @example
 * ```tsx
 * const { datasets, currentDataset, selectDataset } = useDatasetStore();
 *
 * // Select a dataset
 * selectDataset('my-dataset-id');
 *
 * // Access current dataset
 * if (currentDataset) {
 *   console.log(currentDataset.name);
 * }
 * ```
 */
export const useDatasetStore = create<DatasetStore>()(
  devtools(
    (set, get) => ({
      ...initialState,

      setDatasets: (datasets) => {
        set({ datasets, error: null }, false, 'setDatasets')
      },

      selectDataset: (id) => {
        const { datasets } = get()
        const dataset = datasets.find((d) => d.id === id) ?? null
        set({ currentDataset: dataset }, false, 'selectDataset')
      },

      clearSelection: () => {
        set({ currentDataset: null }, false, 'clearSelection')
      },

      setLoading: (isLoading) => {
        set({ isLoading }, false, 'setLoading')
      },

      setError: (error) => {
        set({ error, isLoading: false }, false, 'setError')
      },

      reset: () => {
        set(initialState, false, 'reset')
      },
    }),
    { name: 'dataset-store' },
  ),
)
