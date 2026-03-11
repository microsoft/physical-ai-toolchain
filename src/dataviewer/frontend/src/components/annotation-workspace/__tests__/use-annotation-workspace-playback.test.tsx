import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { useAnnotationWorkspacePlayback } from '@/components/annotation-workspace/useAnnotationWorkspacePlayback'

describe('useAnnotationWorkspacePlayback', () => {
  it('selects a saved subtask, constrains playback to its range, and seeks to its start frame', () => {
    const handleSeekFrame = vi.fn((frame: number) => frame)
    const handleRecordEvent = vi.fn()

    const { result } = renderHook(() => useAnnotationWorkspacePlayback({
      autoLoop: true,
      currentFrame: 0,
      isPlaying: false,
      subtasks: [{ id: 'subtask-1', label: 'Pick', color: '#2563eb', source: 'manual', frameRange: [2, 6] }],
      totalFrames: 12,
      onSeekFrame: handleSeekFrame,
      onResumePlayback: vi.fn(),
      onSetCurrentFrame: vi.fn(),
      onTogglePlayback: vi.fn(),
      onRecordEvent: handleRecordEvent,
    }))

    act(() => {
      result.current.handleSubtaskSelectionChange('subtask-1')
    })

    expect(result.current.selectedSubtaskId).toBe('subtask-1')
    expect(result.current.selectedRange).toBeNull()
    expect(result.current.playbackRange).toEqual([2, 6])
    expect(result.current.shouldLoopPlaybackRange).toBe(true)
    expect(handleSeekFrame).toHaveBeenCalledWith(2, [2, 6], true)
    expect(handleRecordEvent).toHaveBeenCalledWith('subtasks', 'select', { id: 'subtask-1' })
  })

  it('pauses on selection start and resumes after selection completes when playback was already running', () => {
    const handleSeekFrame = vi.fn((frame: number) => frame)
    const handleResumePlayback = vi.fn()
    const handleTogglePlayback = vi.fn()
    const handleRecordEvent = vi.fn()

    const { result } = renderHook(() => useAnnotationWorkspacePlayback({
      autoLoop: false,
      currentFrame: 0,
      isPlaying: true,
      subtasks: [{ id: 'subtask-1', label: 'Pick', color: '#2563eb', source: 'manual', frameRange: [2, 6] }],
      totalFrames: 12,
      onSeekFrame: handleSeekFrame,
      onResumePlayback: handleResumePlayback,
      onSetCurrentFrame: vi.fn(),
      onTogglePlayback: handleTogglePlayback,
      onRecordEvent: handleRecordEvent,
    }))

    act(() => {
      result.current.handleSelectionStart()
    })

    act(() => {
      result.current.handleSelectionComplete([4, 8])
    })

    expect(handleTogglePlayback).toHaveBeenCalledTimes(2)
    expect(handleSeekFrame).toHaveBeenLastCalledWith(4, [4, 8], true)
    expect(handleResumePlayback).toHaveBeenCalledWith(4)
    expect(result.current.selectedSubtaskId).toBeNull()
    expect(result.current.selectedRange).toEqual([4, 8])
    expect(result.current.playbackRange).toEqual([4, 8])
    expect(result.current.shouldLoopPlaybackRange).toBe(false)
    expect(handleRecordEvent).toHaveBeenCalledWith('playback', 'selection-finish', expect.objectContaining({
      rangeStart: 4,
      rangeEnd: 8,
      shouldResume: true,
      nextFrame: 4,
    }))
  })
})
