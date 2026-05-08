import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useBatchSelection, useBatchSelectionStore } from '@/hooks/use-batch-selection'

beforeEach(() => {
  useBatchSelectionStore.setState({
    selectedIndices: new Set<number>(),
    isSelecting: false,
    lastClickedIndex: null,
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useBatchSelection', () => {
  it('starts with an empty selection', () => {
    const { result } = renderHook(() => useBatchSelection())

    expect(result.current.selectedCount).toBe(0)
    expect(result.current.hasSelection).toBe(false)
    expect(result.current.selectedArray).toEqual([])
    expect(result.current.isSelecting).toBe(false)
    expect(result.current.lastClickedIndex).toBeNull()
  })

  it('toggleSelection adds an index and updates lastClickedIndex', () => {
    const { result } = renderHook(() => useBatchSelection())

    act(() => {
      result.current.toggleSelection(2)
    })

    expect(result.current.isSelected(2)).toBe(true)
    expect(result.current.selectedCount).toBe(1)
    expect(result.current.hasSelection).toBe(true)
    expect(result.current.lastClickedIndex).toBe(2)
  })

  it('toggleSelection removes an index when called twice', () => {
    const { result } = renderHook(() => useBatchSelection())

    act(() => {
      result.current.toggleSelection(5)
    })
    act(() => {
      result.current.toggleSelection(5)
    })

    expect(result.current.isSelected(5)).toBe(false)
    expect(result.current.selectedCount).toBe(0)
  })

  it('selectRange selects an inclusive range and accepts reversed bounds', () => {
    const { result } = renderHook(() => useBatchSelection())

    act(() => {
      result.current.selectRange(4, 1)
    })

    expect(result.current.selectedArray).toEqual([1, 2, 3, 4])
    expect(result.current.lastClickedIndex).toBe(1)
  })

  it('selectAll replaces the current selection', () => {
    const { result } = renderHook(() => useBatchSelection())

    act(() => {
      result.current.toggleSelection(99)
    })
    act(() => {
      result.current.selectAll([10, 20, 30])
    })

    expect(result.current.selectedArray).toEqual([10, 20, 30])
    expect(result.current.isSelected(99)).toBe(false)
  })

  it('clearSelection empties selection and resets lastClickedIndex', () => {
    const { result } = renderHook(() => useBatchSelection())

    act(() => {
      result.current.selectAll([1, 2, 3])
    })
    act(() => {
      result.current.setLastClickedIndex(2)
    })
    act(() => {
      result.current.clearSelection()
    })

    expect(result.current.selectedCount).toBe(0)
    expect(result.current.lastClickedIndex).toBeNull()
  })

  it('setSelecting toggles selection mode', () => {
    const { result } = renderHook(() => useBatchSelection())

    act(() => {
      result.current.setSelecting(true)
    })

    expect(result.current.isSelecting).toBe(true)
  })

  it('selectedArray is sorted ascending regardless of insertion order', () => {
    const { result } = renderHook(() => useBatchSelection())

    act(() => {
      result.current.toggleSelection(7)
    })
    act(() => {
      result.current.toggleSelection(1)
    })
    act(() => {
      result.current.toggleSelection(4)
    })

    expect(result.current.selectedArray).toEqual([1, 4, 7])
  })
})
