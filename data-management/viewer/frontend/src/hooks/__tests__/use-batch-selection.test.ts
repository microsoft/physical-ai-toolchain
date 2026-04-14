import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { useBatchSelection, useBatchSelectionStore } from '../use-batch-selection'

function resetStore() {
  const state = useBatchSelectionStore.getState()
  state.clearSelection()
  state.setSelecting(false)
  state.setLastClickedIndex(null)
}

describe('useBatchSelectionStore', () => {
  beforeEach(() => {
    resetStore()
  })

  it('starts with empty selection', () => {
    const state = useBatchSelectionStore.getState()
    expect(state.selectedIndices.size).toBe(0)
    expect(state.isSelecting).toBe(false)
    expect(state.lastClickedIndex).toBeNull()
  })

  describe('toggleSelection', () => {
    it('adds index when not selected', () => {
      useBatchSelectionStore.getState().toggleSelection(5)
      const state = useBatchSelectionStore.getState()
      expect(state.selectedIndices.has(5)).toBe(true)
      expect(state.lastClickedIndex).toBe(5)
    })

    it('removes index when already selected', () => {
      useBatchSelectionStore.getState().toggleSelection(5)
      useBatchSelectionStore.getState().toggleSelection(5)
      expect(useBatchSelectionStore.getState().selectedIndices.has(5)).toBe(false)
    })
  })

  describe('selectRange', () => {
    it('selects inclusive range', () => {
      useBatchSelectionStore.getState().selectRange(2, 5)
      const indices = useBatchSelectionStore.getState().selectedIndices
      expect(indices.size).toBe(4)
      expect(indices.has(2)).toBe(true)
      expect(indices.has(3)).toBe(true)
      expect(indices.has(4)).toBe(true)
      expect(indices.has(5)).toBe(true)
    })

    it('handles reversed range (start > end)', () => {
      useBatchSelectionStore.getState().selectRange(5, 2)
      const indices = useBatchSelectionStore.getState().selectedIndices
      expect(indices.size).toBe(4)
      expect(indices.has(2)).toBe(true)
      expect(indices.has(5)).toBe(true)
    })

    it('adds to existing selection', () => {
      useBatchSelectionStore.getState().toggleSelection(0)
      useBatchSelectionStore.getState().selectRange(3, 5)
      const indices = useBatchSelectionStore.getState().selectedIndices
      expect(indices.has(0)).toBe(true)
      expect(indices.has(3)).toBe(true)
    })

    it('sets lastClickedIndex to endIndex', () => {
      useBatchSelectionStore.getState().selectRange(2, 7)
      expect(useBatchSelectionStore.getState().lastClickedIndex).toBe(7)
    })
  })

  describe('selectAll', () => {
    it('replaces selection with provided indices', () => {
      useBatchSelectionStore.getState().toggleSelection(99)
      useBatchSelectionStore.getState().selectAll([1, 2, 3])
      const indices = useBatchSelectionStore.getState().selectedIndices
      expect(indices.size).toBe(3)
      expect(indices.has(99)).toBe(false)
    })
  })

  describe('clearSelection', () => {
    it('empties selection and resets lastClickedIndex', () => {
      useBatchSelectionStore.getState().selectAll([1, 2, 3])
      useBatchSelectionStore.getState().clearSelection()
      const state = useBatchSelectionStore.getState()
      expect(state.selectedIndices.size).toBe(0)
      expect(state.lastClickedIndex).toBeNull()
    })
  })

  describe('isSelected', () => {
    it('returns true for selected index', () => {
      useBatchSelectionStore.getState().toggleSelection(3)
      expect(useBatchSelectionStore.getState().isSelected(3)).toBe(true)
    })

    it('returns false for unselected index', () => {
      expect(useBatchSelectionStore.getState().isSelected(3)).toBe(false)
    })
  })
})

describe('useBatchSelection', () => {
  beforeEach(() => {
    resetStore()
  })

  it('returns computed properties', () => {
    useBatchSelectionStore.getState().selectAll([5, 2, 8])
    const { result } = renderHook(() => useBatchSelection())

    expect(result.current.selectedCount).toBe(3)
    expect(result.current.hasSelection).toBe(true)
    expect(result.current.selectedArray).toEqual([2, 5, 8])
  })

  it('returns zero count when empty', () => {
    const { result } = renderHook(() => useBatchSelection())
    expect(result.current.selectedCount).toBe(0)
    expect(result.current.hasSelection).toBe(false)
    expect(result.current.selectedArray).toEqual([])
  })

  it('exposes store actions', () => {
    const { result } = renderHook(() => useBatchSelection())

    act(() => {
      result.current.toggleSelection(1)
    })

    expect(result.current.selectedCount).toBe(1)
    expect(result.current.isSelected(1)).toBe(true)
  })
})
