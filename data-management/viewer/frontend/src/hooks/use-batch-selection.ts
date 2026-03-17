/**
 * Batch selection hook for managing selected episodes.
 */

import { create } from 'zustand'
import { devtools } from 'zustand/middleware'

interface BatchSelectionState {
  /** Set of selected episode indices */
  selectedIndices: Set<number>
  /** Whether selection mode is active */
  isSelecting: boolean
  /** Last clicked index for shift-select */
  lastClickedIndex: number | null
}

interface BatchSelectionActions {
  /** Toggle selection of a single episode */
  toggleSelection: (index: number) => void
  /** Select a range of episodes (for shift-click) */
  selectRange: (startIndex: number, endIndex: number) => void
  /** Select all episodes in the provided range */
  selectAll: (indices: number[]) => void
  /** Clear all selections */
  clearSelection: () => void
  /** Check if an episode is selected */
  isSelected: (index: number) => boolean
  /** Set last clicked index */
  setLastClickedIndex: (index: number | null) => void
  /** Enable/disable selection mode */
  setSelecting: (isSelecting: boolean) => void
}

type BatchSelectionStore = BatchSelectionState & BatchSelectionActions

/**
 * Zustand store for batch selection state.
 */
export const useBatchSelectionStore = create<BatchSelectionStore>()(
  devtools(
    (set, get) => ({
      selectedIndices: new Set(),
      isSelecting: false,
      lastClickedIndex: null,

      toggleSelection: (index) => {
        const { selectedIndices } = get()
        const newSet = new Set(selectedIndices)
        if (newSet.has(index)) {
          newSet.delete(index)
        } else {
          newSet.add(index)
        }
        set({ selectedIndices: newSet, lastClickedIndex: index }, false, 'toggleSelection')
      },

      selectRange: (startIndex, endIndex) => {
        const { selectedIndices } = get()
        const newSet = new Set(selectedIndices)
        const [min, max] = [Math.min(startIndex, endIndex), Math.max(startIndex, endIndex)]
        for (let i = min; i <= max; i++) {
          newSet.add(i)
        }
        set({ selectedIndices: newSet, lastClickedIndex: endIndex }, false, 'selectRange')
      },

      selectAll: (indices) => {
        set({ selectedIndices: new Set(indices) }, false, 'selectAll')
      },

      clearSelection: () => {
        set({ selectedIndices: new Set(), lastClickedIndex: null }, false, 'clearSelection')
      },

      isSelected: (index) => {
        return get().selectedIndices.has(index)
      },

      setLastClickedIndex: (index) => {
        set({ lastClickedIndex: index }, false, 'setLastClickedIndex')
      },

      setSelecting: (isSelecting) => {
        set({ isSelecting }, false, 'setSelecting')
      },
    }),
    { name: 'batch-selection-store' },
  ),
)

/**
 * Hook for batch selection with computed values.
 */
export function useBatchSelection() {
  const store = useBatchSelectionStore()

  return {
    ...store,
    selectedCount: store.selectedIndices.size,
    hasSelection: store.selectedIndices.size > 0,
    selectedArray: Array.from(store.selectedIndices).sort((a, b) => a - b),
  }
}
