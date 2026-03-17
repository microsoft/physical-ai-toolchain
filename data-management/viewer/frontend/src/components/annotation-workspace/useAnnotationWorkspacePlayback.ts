import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { DataviewerDiagnosticEvent } from '@/lib/playback-diagnostics'
import {
  clampFrameToPlaybackRange,
  resolvePlaybackRange,
  shouldLoopActivePlaybackRange,
} from '@/lib/playback-utils'
import type { SubtaskSegment } from '@/types/episode-edit'

interface UseAnnotationWorkspacePlaybackOptions {
  autoLoop: boolean
  currentFrame: number
  currentDatasetId?: string | null
  currentEpisodeIndex?: number | null
  isPlaying: boolean
  subtasks: SubtaskSegment[]
  totalFrames: number
  onSeekFrame: (frame: number, range: [number, number] | null, constrainToRange?: boolean) => number
  onResumePlayback: (frame: number) => void
  onTogglePlayback: () => void
  onSetCurrentFrame: (frame: number) => void
  onRecordEvent: (
    channel: DataviewerDiagnosticEvent['channel'],
    type: DataviewerDiagnosticEvent['type'],
    data?: DataviewerDiagnosticEvent['data'],
  ) => void
}

export function useAnnotationWorkspacePlayback({
  autoLoop,
  currentFrame,
  currentDatasetId,
  currentEpisodeIndex,
  isPlaying,
  subtasks,
  totalFrames,
  onSeekFrame,
  onResumePlayback,
  onTogglePlayback,
  onSetCurrentFrame,
  onRecordEvent,
}: UseAnnotationWorkspacePlaybackOptions) {
  const [selectedSubtaskId, setSelectedSubtaskId] = useState<string | null>(null)
  const [selectedRange, setSelectedRange] = useState<[number, number] | null>(null)
  const shouldResumeAfterSelectionRef = useRef(false)

  const activeSubtask = useMemo(
    () => subtasks.find((segment) => segment.id === selectedSubtaskId) ?? null,
    [selectedSubtaskId, subtasks],
  )
  const activePlaybackRange = activeSubtask?.frameRange ?? selectedRange
  const shouldLoopPlaybackRange = useMemo(
    () => shouldLoopActivePlaybackRange(activePlaybackRange, autoLoop),
    [activePlaybackRange, autoLoop],
  )
  const playbackRange = useMemo(
    () => resolvePlaybackRange(totalFrames, activePlaybackRange),
    [activePlaybackRange, totalFrames],
  )
  const playbackRangeStart = playbackRange[0]
  const playbackRangeEnd = playbackRange[1]
  const playbackRangeLabel = selectedSubtaskId
    ? 'Active subtask range'
    : selectedRange
      ? 'Draft selection range'
      : null
  const playbackRangeHighlight = useMemo(() => {
    if (!activePlaybackRange || totalFrames <= 1) {
      return null
    }

    const total = Math.max(totalFrames - 1, 1)
    const left = (playbackRangeStart / total) * 100
    const width = ((Math.max(playbackRangeEnd - playbackRangeStart, 0) + 1) / (total + 1)) * 100

    return {
      left: `${left}%`,
      width: `${Math.max(width, 0.5)}%`,
    }
  }, [activePlaybackRange, playbackRangeEnd, playbackRangeStart, totalFrames])

  const setFrameWithinPlaybackRange = useCallback(
    (frame: number, rangeOverride?: [number, number] | null) => {
      return onSeekFrame(frame, rangeOverride ?? activePlaybackRange, true)
    },
    [activePlaybackRange, onSeekFrame],
  )

  const clearPlaybackSelection = useCallback(() => {
    shouldResumeAfterSelectionRef.current = false
    setSelectedSubtaskId(null)
    setSelectedRange(null)
    onRecordEvent('playback', 'selection-clear', { source: 'workspace-action' })
  }, [onRecordEvent])

  const handleGraphSeek = useCallback(
    (frame: number) => {
      onRecordEvent('playback', 'graph-seek', { frame })
      onSeekFrame(frame, null, false)
    },
    [onRecordEvent, onSeekFrame],
  )

  const handleSubtaskSelectionChange = useCallback(
    (id: string | null) => {
      setSelectedSubtaskId(id)
      shouldResumeAfterSelectionRef.current = false
      onRecordEvent('subtasks', 'select', { id })

      if (!id) {
        setSelectedRange(null)
        return
      }

      setSelectedRange(null)
      const nextSegment = subtasks.find((segment) => segment.id === id)

      if (nextSegment) {
        setFrameWithinPlaybackRange(nextSegment.frameRange[0], nextSegment.frameRange)
      }
    },
    [onRecordEvent, setFrameWithinPlaybackRange, subtasks],
  )

  const handleCreateSubtaskFromRange = useCallback(
    (segment: SubtaskSegment) => {
      shouldResumeAfterSelectionRef.current = false
      setSelectedRange(null)
      setSelectedSubtaskId(segment.id)
      setFrameWithinPlaybackRange(segment.frameRange[0], segment.frameRange)
      onRecordEvent('subtasks', 'create', {
        id: segment.id,
        rangeStart: segment.frameRange[0],
        rangeEnd: segment.frameRange[1],
      })
    },
    [onRecordEvent, setFrameWithinPlaybackRange],
  )

  const handleDraftRangeChange = useCallback(
    (range: [number, number] | null) => {
      if (!range) {
        shouldResumeAfterSelectionRef.current = false
      }

      setSelectedSubtaskId(null)
      setSelectedRange(range)
      onRecordEvent('playback', 'draft-range-change', {
        rangeStart: range?.[0] ?? null,
        rangeEnd: range?.[1] ?? null,
      })
    },
    [onRecordEvent],
  )

  const handleSelectionStart = useCallback(() => {
    shouldResumeAfterSelectionRef.current = isPlaying
    onRecordEvent('playback', 'selection-start', { shouldResume: isPlaying })

    if (isPlaying) {
      onTogglePlayback()
    }
  }, [isPlaying, onRecordEvent, onTogglePlayback])

  const handleSelectionComplete = useCallback(
    (range: [number, number]) => {
      const shouldResume = shouldResumeAfterSelectionRef.current
      const nextFrame = setFrameWithinPlaybackRange(range[0], range)

      setSelectedSubtaskId(null)
      setSelectedRange(range)
      shouldResumeAfterSelectionRef.current = false
      onRecordEvent('playback', 'selection-finish', {
        shouldResume,
        rangeStart: range[0],
        rangeEnd: range[1],
        nextFrame,
      })

      if (shouldResume) {
        onTogglePlayback()
        onResumePlayback(nextFrame)
      }
    },
    [onRecordEvent, onResumePlayback, onTogglePlayback, setFrameWithinPlaybackRange],
  )

  const stepFrame = useCallback(
    (delta: number) => {
      setFrameWithinPlaybackRange(currentFrame + delta)
    },
    [currentFrame, setFrameWithinPlaybackRange],
  )

  useEffect(() => {
    setSelectedSubtaskId(null)
    setSelectedRange(null)
    shouldResumeAfterSelectionRef.current = false
  }, [currentDatasetId, currentEpisodeIndex])

  useEffect(() => {
    if (selectedSubtaskId && !activeSubtask) {
      setSelectedSubtaskId(null)
    }
  }, [activeSubtask, selectedSubtaskId])

  useEffect(() => {
    if (!activePlaybackRange) {
      return
    }

    const clampedFrame = clampFrameToPlaybackRange(currentFrame, totalFrames, activePlaybackRange)

    if (clampedFrame !== currentFrame) {
      onSetCurrentFrame(clampedFrame)
    }
  }, [activePlaybackRange, currentFrame, onSetCurrentFrame, totalFrames])

  return {
    activePlaybackRange,
    activeSubtask,
    clearPlaybackSelection,
    handleCreateSubtaskFromRange,
    handleDraftRangeChange,
    handleGraphSeek,
    handleSelectionComplete,
    handleSelectionStart,
    handleSubtaskSelectionChange,
    playbackRange,
    playbackRangeEnd,
    playbackRangeHighlight,
    playbackRangeLabel,
    playbackRangeStart,
    selectedRange,
    selectedSubtaskId,
    setFrameWithinPlaybackRange,
    shouldLoopPlaybackRange,
    stepFrame,
  }
}
