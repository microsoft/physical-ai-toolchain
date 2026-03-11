import { useCallback, useEffect, useRef, useState } from 'react'

import type { DataviewerDiagnosticEvent } from '@/lib/playback-diagnostics'

interface UseTrajectoryPlotSelectionOptions {
  currentEpisodeLength: number
  frameFromClientX: (clientX: number) => number
  getSurfaceBounds?: () => { left: number; top: number } | null
  selectedRange: [number, number] | null
  onCreateSubtaskFromRange?: (range: [number, number]) => void
  onSelectedRangeChange?: (range: [number, number] | null) => void
  onSelectionStart?: () => void
  onSelectionComplete?: (range: [number, number]) => void
  onSeekFrame?: (frame: number) => void
  onSetCurrentFrame: (frame: number) => void
  onRecordEvent: (
    channel: DataviewerDiagnosticEvent['channel'],
    type: DataviewerDiagnosticEvent['type'],
    data?: DataviewerDiagnosticEvent['data'],
  ) => void
}

export function useTrajectoryPlotSelection({
  currentEpisodeLength,
  frameFromClientX,
  getSurfaceBounds,
  selectedRange,
  onSelectedRangeChange,
  onSelectionStart,
  onSelectionComplete,
  onSeekFrame,
  onSetCurrentFrame,
  onRecordEvent,
}: UseTrajectoryPlotSelectionOptions) {
  const selectionAnchorFrameRef = useRef<number | null>(null)
  const selectionAnchorXRef = useRef<number | null>(null)
  const selectionDraggingRef = useRef(false)
  const [selectionDragging, setSelectionDragging] = useState(false)
  const [contextMenuPosition, setContextMenuPosition] = useState<{ x: number; y: number } | null>(null)

  useEffect(() => {
    if (!selectedRange) {
      setContextMenuPosition(null)
    }
  }, [selectedRange])

  useEffect(() => {
    if (!selectedRange) {
      return
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return
      }

      onRecordEvent('playback', 'selection-clear', { source: 'escape' })
      setContextMenuPosition(null)
      onSelectedRangeChange?.(null)
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [onRecordEvent, onSelectedRangeChange, selectedRange])

  const updateSelectedRange = useCallback((startFrame: number, endFrame: number) => {
    onSelectedRangeChange?.([
      Math.min(startFrame, endFrame),
      Math.max(startFrame, endFrame),
    ])
  }, [onSelectedRangeChange])

  const dismissContextMenu = useCallback(() => {
    setContextMenuPosition(null)
  }, [])

  const handleSelectionPointerDown = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) {
      return
    }

    const anchorFrame = frameFromClientX(event.clientX)

    if ('setPointerCapture' in event.currentTarget) {
      event.currentTarget.setPointerCapture(event.pointerId)
    }

    setContextMenuPosition(null)
    selectionDraggingRef.current = false
    setSelectionDragging(false)
    selectionAnchorXRef.current = event.clientX
    selectionAnchorFrameRef.current = anchorFrame
    onSetCurrentFrame(anchorFrame)
    onRecordEvent('playback', 'selection-anchor', { anchorFrame })
    onSeekFrame?.(anchorFrame)
  }, [frameFromClientX, onRecordEvent, onSeekFrame, onSetCurrentFrame])

  const handleSelectionPointerMove = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (selectionAnchorFrameRef.current === null || selectionAnchorXRef.current === null) {
      return
    }

    if (Math.abs(event.clientX - selectionAnchorXRef.current) < 4) {
      return
    }

    if (!selectionDraggingRef.current) {
      selectionDraggingRef.current = true
      setSelectionDragging(true)
      onRecordEvent('playback', 'selection-drag-start', { anchorFrame: selectionAnchorFrameRef.current })
      onSelectionStart?.()
    }

    const pointerFrame = frameFromClientX(event.clientX)
    onRecordEvent('playback', 'selection-drag-update', {
      anchorFrame: selectionAnchorFrameRef.current,
      currentFrame: pointerFrame,
    })
    updateSelectedRange(selectionAnchorFrameRef.current, pointerFrame)
  }, [frameFromClientX, onRecordEvent, onSelectionStart, updateSelectedRange])

  const handleSelectionPointerUp = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (selectionAnchorFrameRef.current === null) {
      return
    }

    if ('hasPointerCapture' in event.currentTarget && event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }

    const pointerFrame = frameFromClientX(event.clientX)
    const pointerDistance = selectionAnchorXRef.current === null
      ? 0
      : Math.abs(event.clientX - selectionAnchorXRef.current)

    if (pointerDistance >= 4 || selectionDraggingRef.current) {
      const nextRange: [number, number] = [
        Math.min(selectionAnchorFrameRef.current, pointerFrame),
        Math.max(selectionAnchorFrameRef.current, pointerFrame),
      ]

      onRecordEvent('playback', 'selection-complete', {
        rangeStart: nextRange[0],
        rangeEnd: nextRange[1],
      })
      updateSelectedRange(nextRange[0], nextRange[1])
      onSelectionComplete?.(nextRange)
    }

    selectionDraggingRef.current = false
    selectionAnchorFrameRef.current = null
    selectionAnchorXRef.current = null
    setSelectionDragging(false)
  }, [frameFromClientX, onRecordEvent, onSelectionComplete, updateSelectedRange])

  const handleSelectionContextMenu = useCallback((event: React.MouseEvent<HTMLDivElement>) => {
    if (!selectedRange || currentEpisodeLength <= 1) {
      return
    }

    const frame = frameFromClientX(event.clientX)
    if (frame < selectedRange[0] || frame > selectedRange[1]) {
      return
    }

    const bounds = getSurfaceBounds?.() ?? { left: 0, top: 0 }
    if (!bounds) {
      return
    }

    event.preventDefault()
    setContextMenuPosition({ x: event.clientX - bounds.left, y: event.clientY - bounds.top })
  }, [currentEpisodeLength, frameFromClientX, getSurfaceBounds, selectedRange])

  return {
    contextMenuPosition,
    selectionDragging,
    dismissContextMenu,
    handleSelectionPointerDown,
    handleSelectionPointerMove,
    handleSelectionPointerUp,
    handleSelectionContextMenu,
  }
}
