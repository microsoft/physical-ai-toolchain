import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { useTrajectoryPlotSelection } from '@/components/episode-viewer/useTrajectoryPlotSelection'

describe('useTrajectoryPlotSelection', () => {
  it('starts drag selection after the pointer passes the drag threshold and updates the selected range', () => {
    const handleSelectedRangeChange = vi.fn()
    const handleSelectionStart = vi.fn()
    const handleSeekFrame = vi.fn()
    const handleSetCurrentFrame = vi.fn()
    const handleRecordEvent = vi.fn()

    const { result } = renderHook(() => useTrajectoryPlotSelection({
      currentEpisodeLength: 20,
      frameFromClientX: (clientX) => Math.round(clientX / 10),
      selectedRange: null,
      onSelectedRangeChange: handleSelectedRangeChange,
      onSelectionStart: handleSelectionStart,
      onSelectionComplete: vi.fn(),
      onCreateSubtaskFromRange: vi.fn(),
      onSeekFrame: handleSeekFrame,
      onSetCurrentFrame: handleSetCurrentFrame,
      onRecordEvent: handleRecordEvent,
    }))

    act(() => {
      result.current.handleSelectionPointerDown({
        button: 0,
        clientX: 20,
        pointerId: 1,
        currentTarget: { setPointerCapture: vi.fn() },
      } as unknown as React.PointerEvent<HTMLDivElement>)
    })

    act(() => {
      result.current.handleSelectionPointerMove({
        clientX: 90,
      } as unknown as React.PointerEvent<HTMLDivElement>)
    })

    expect(handleSelectionStart).toHaveBeenCalledOnce()
    expect(handleSelectedRangeChange).toHaveBeenLastCalledWith([2, 9])
    expect(handleSetCurrentFrame).toHaveBeenCalledWith(2)
    expect(handleSeekFrame).toHaveBeenCalledWith(2)
  })

  it('commits the selected range on pointer up and exposes a dismissible context menu position', () => {
    const handleSelectionComplete = vi.fn()
    const handleSelectedRangeChange = vi.fn()

    const { result, rerender } = renderHook(
      ({ selectedRange }) => useTrajectoryPlotSelection({
        currentEpisodeLength: 20,
        frameFromClientX: (clientX) => Math.round(clientX / 10),
        selectedRange,
        onSelectedRangeChange: handleSelectedRangeChange,
        onSelectionStart: vi.fn(),
        onSelectionComplete: handleSelectionComplete,
        onCreateSubtaskFromRange: vi.fn(),
        onSeekFrame: vi.fn(),
        onSetCurrentFrame: vi.fn(),
        onRecordEvent: vi.fn(),
      }),
      { initialProps: { selectedRange: [2, 9] as [number, number] | null } },
    )

    act(() => {
      result.current.handleSelectionPointerDown({
        button: 0,
        clientX: 20,
        pointerId: 1,
        currentTarget: { setPointerCapture: vi.fn() },
      } as unknown as React.PointerEvent<HTMLDivElement>)
      result.current.handleSelectionPointerMove({ clientX: 100 } as unknown as React.PointerEvent<HTMLDivElement>)
      result.current.handleSelectionPointerUp({
        clientX: 100,
        pointerId: 1,
        currentTarget: { hasPointerCapture: () => true, releasePointerCapture: vi.fn() },
      } as unknown as React.PointerEvent<HTMLDivElement>)
    })

    expect(handleSelectionComplete).toHaveBeenCalledWith([2, 10])

    act(() => {
      result.current.handleSelectionContextMenu({
        clientX: 45,
        clientY: 60,
        preventDefault: vi.fn(),
      } as unknown as React.MouseEvent<HTMLDivElement>)
    })

    expect(result.current.contextMenuPosition).toEqual({ x: 45, y: 60 })

    rerender({ selectedRange: null })
    expect(result.current.contextMenuPosition).toBeNull()
  })
})
