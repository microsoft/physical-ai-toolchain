import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useBatchSelection, useBatchSelectionStore } from '@/hooks/use-batch-selection'

beforeEach(() => {
  useBatchSelectionStore.setState({
    selectedIndices: new Set(),
    isSelecting: false,
    lastClickedIndex: null,
  })
})

afterEach(() => {
  useBatchSelectionStore.setState({
    selectedIndices: new Set(),
    isSelecting: false,
    lastClickedIndex: null,
  })
})

describe('useBatchSelection', () => {
  it('starts empty', () => {
    const { result } = renderHook(() => useBatchSelection())
    expect(result.current.selectedCount).toBe(0)
    expect(result.current.hasSelection).toBe(false)
    expect(result.current.selectedArray).toEqual([])
    expect(result.current.isSelecting).toBe(false)
  })

  it('toggleSelection adds and removes an index and updates lastClickedIndex', () => {
    const { result } = renderHook(() => useBatchSelection())

    act(() => result.current.toggleSelection(3))
    expect(result.current.selectedArray).toEqual([3])
    expect(result.current.hasSelection).toBe(true)
    expect(result.current.lastClickedIndex).toBe(3)
    expect(result.current.isSelected(3)).toBe(true)
    expect(result.current.isSelected(4)).toBe(false)

    act(() => result.current.toggleSelection(3))
    expect(result.current.selectedArray).toEqual([])
    expect(result.current.hasSelection).toBe(false)
  })

  it('selectRange selects inclusive range regardless of order and tracks endIndex', () => {
    const { result } = renderHook(() => useBatchSelection())

    act(() => result.current.selectRange(5, 2))
    expect(result.current.selectedArray).toEqual([2, 3, 4, 5])
    expect(result.current.lastClickedIndex).toBe(2)

    act(() => result.current.selectRange(7, 9))
    expect(result.current.selectedArray).toEqual([2, 3, 4, 5, 7, 8, 9])
    expect(result.current.lastClickedIndex).toBe(9)
  })

  it('selectAll replaces selection with the provided indices', () => {
    const { result } = renderHook(() => useBatchSelection())

    act(() => result.current.toggleSelection(99))
    act(() => result.current.selectAll([3, 1, 2]))

    expect(result.current.selectedArray).toEqual([1, 2, 3])
    expect(result.current.selectedCount).toBe(3)
  })

  it('clearSelection empties selection and resets lastClickedIndex', () => {
    const { result } = renderHook(() => useBatchSelection())

    act(() => result.current.selectRange(1, 4))
    expect(result.current.selectedCount).toBe(4)

    act(() => result.current.clearSelection())
    expect(result.current.selectedArray).toEqual([])
    expect(result.current.lastClickedIndex).toBeNull()
  })

  it('setLastClickedIndex updates only that field', () => {
    const { result } = renderHook(() => useBatchSelection())

    act(() => result.current.setLastClickedIndex(7))
    expect(result.current.lastClickedIndex).toBe(7)

    act(() => result.current.setLastClickedIndex(null))
    expect(result.current.lastClickedIndex).toBeNull()
  })

  it('setSelecting toggles the isSelecting flag', () => {
    const { result } = renderHook(() => useBatchSelection())

    act(() => result.current.setSelecting(true))
    expect(result.current.isSelecting).toBe(true)

    act(() => result.current.setSelecting(false))
    expect(result.current.isSelecting).toBe(false)
  })

  it('selectedArray returns sorted ascending order', () => {
    const { result } = renderHook(() => useBatchSelection())

    act(() => result.current.selectAll([10, 1, 5, 2]))
    expect(result.current.selectedArray).toEqual([1, 2, 5, 10])
  })
})
