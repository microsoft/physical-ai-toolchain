import { act, renderHook } from '@testing-library/react'
import type { SyntheticEvent } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAnnotationWorkspaceVideoSync } from '@/components/annotation-workspace/useAnnotationWorkspaceVideoSync'
import type { FrameInsertion } from '@/types/episode-edit'

function playbackOptions(
  overrides: Partial<Parameters<typeof useAnnotationWorkspaceVideoSync>[0]> = {},
): Parameters<typeof useAnnotationWorkspaceVideoSync>[0] {
  return {
    currentFrame: 0,
    totalFrames: 10,
    originalFrameIndex: 0,
    activePlaybackRange: null,
    playbackRangeStart: 0,
    playbackRangeEnd: 9,
    isPlaying: false,
    playbackSpeed: 1,
    autoPlay: false,
    autoLoop: false,
    shouldLoopPlaybackRange: false,
    datasetFps: 10,
    insertedFrames: new Map(),
    removedFrames: new Set(),
    videoSrc: null,
    onSetCurrentFrame: vi.fn(),
    onTogglePlayback: vi.fn(),
    onSetFrameWithinPlaybackRange: vi.fn(),
    onRecordEvent: vi.fn(),
    ...overrides,
  }
}

describe('useAnnotationWorkspaceVideoSync', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('pauses normal playback synchronization while the document is hidden', () => {
    const options = playbackOptions({ videoSrc: '/source.mp4' })
    const { result, rerender, unmount } = renderHook(
      ({ isPlaying }) => useAnnotationWorkspaceVideoSync({ ...options, isPlaying }),
      { initialProps: { isPlaying: false } },
    )
    const video = document.createElement('video')
    video.pause = vi.fn()
    video.play = vi.fn(() => Promise.resolve())
    result.current.videoRef.current = video
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)

    rerender({ isPlaying: true })

    expect(video.pause).toHaveBeenCalled()
    expect(video.play).not.toHaveBeenCalled()
    expect(options.onRecordEvent).not.toHaveBeenCalledWith(
      'playback',
      'sync-action',
      expect.anything(),
    )
    unmount()
  })

  it('does not resume or seek video playback while the document is hidden', () => {
    const options = playbackOptions({ videoSrc: '/source.mp4' })
    const { result, unmount } = renderHook(() => useAnnotationWorkspaceVideoSync(options))
    const video = document.createElement('video')
    video.pause = vi.fn()
    video.play = vi.fn(() => Promise.resolve())
    result.current.videoRef.current = video
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)

    act(() => {
      result.current.handleResumePlayback(3)
      vi.advanceTimersByTime(20)
    })

    expect(video.pause).toHaveBeenCalledOnce()
    expect(video.play).not.toHaveBeenCalled()
    expect(video.currentTime).toBe(0)
    unmount()
  })

  it.each([Number.NaN, Number.POSITIVE_INFINITY])(
    'resumes media with a non-finite duration: %s',
    (duration) => {
      const { result, unmount } = renderHook(() =>
        useAnnotationWorkspaceVideoSync(playbackOptions({ videoSrc: '/source.mp4' })),
      )
      const video = document.createElement('video')
      Object.defineProperty(video, 'duration', { configurable: true, value: duration })
      video.pause = vi.fn()
      video.play = vi.fn(() => Promise.resolve())
      result.current.videoRef.current = video

      act(() => {
        result.current.handleResumePlayback(3)
        vi.advanceTimersByTime(20)
      })

      expect(video.currentTime).toBeCloseTo(0.301)
      expect(video.play).toHaveBeenCalledOnce()
      unmount()
    },
  )

  it.each([false, true])(
    'gates frame-only autoplay on document visibility: hidden=%s',
    (hidden) => {
      vi.spyOn(document, 'hidden', 'get').mockReturnValue(hidden)
      const options = playbackOptions({ autoPlay: true })
      const { unmount } = renderHook(() => useAnnotationWorkspaceVideoSync(options))

      expect(options.onTogglePlayback).toHaveBeenCalledTimes(hidden ? 0 : 1)
      unmount()
    },
  )

  it.each([false, true])(
    'advances frame-only playback and %s loop behavior within selected bounds',
    (loop) => {
      const options = playbackOptions({
        isPlaying: true,
        currentFrame: 2,
        originalFrameIndex: 2,
        activePlaybackRange: [2, 4],
        playbackRangeStart: 2,
        playbackRangeEnd: 4,
        shouldLoopPlaybackRange: loop,
        autoLoop: loop,
      })
      const { rerender, unmount } = renderHook((props) => useAnnotationWorkspaceVideoSync(props), {
        initialProps: options,
      })
      for (let step = 0; step < 40; step++) {
        act(() => vi.advanceTimersByTime(20))
        const currentFrame = vi.mocked(options.onSetCurrentFrame).mock.lastCall?.[0] ?? 2
        rerender({ ...options, currentFrame, originalFrameIndex: currentFrame })
      }
      const frames = vi.mocked(options.onSetCurrentFrame).mock.calls.map(([frame]) => frame)
      expect(frames.length).toBeGreaterThan(0)
      expect(frames.every((frame) => frame >= 2 && frame <= 4)).toBe(true)
      if (loop) expect(options.onTogglePlayback).not.toHaveBeenCalled()
      else expect(options.onTogglePlayback).toHaveBeenCalledOnce()
      unmount()
    },
  )

  it('seeks with and without a video and resumes at the selected source window offset', () => {
    const options = playbackOptions({ videoSrc: '/source.mp4', videoWindow: [2, 3] })
    const { result, rerender, unmount } = renderHook(
      (props) => useAnnotationWorkspaceVideoSync(props),
      { initialProps: options },
    )
    act(() => {
      expect(result.current.seekVideoFrame(-5, null, false)).toBe(0)
    })
    act(() => {
      result.current.handleResumePlayback(0)
      vi.advanceTimersByTime(20)
    })
    const video = document.createElement('video')
    Object.defineProperty(video, 'duration', { value: 10, configurable: true })
    Object.defineProperty(video, 'currentTime', { value: 0, configurable: true, writable: true })
    video.pause = vi.fn()
    video.play = vi.fn(() => Promise.resolve())
    result.current.videoRef.current = video
    act(() =>
      result.current.handleLoadedMetadata({
        currentTarget: video,
      } as SyntheticEvent<HTMLVideoElement>),
    )
    act(() => {
      expect(result.current.seekVideoFrame(9, [2, 4])).toBe(4)
    })
    expect(video.currentTime).toBeCloseTo(2.4)
    rerender({ ...options, isPlaying: true, playbackSpeed: 2 })
    act(() => {
      result.current.handleResumePlayback(3)
      vi.advanceTimersByTime(20)
    })
    expect(video.currentTime).toBeCloseTo(2.301)
    expect(video.playbackRate).toBe(2)
    expect(video.play).toHaveBeenCalled()
    unmount()
  })

  it.each([false, true])(
    'stops or loops at the end of a concatenated source window (loop=%s)',
    (loop) => {
      const options = playbackOptions({
        isPlaying: true,
        videoSrc: '/source.mp4',
        videoWindow: [2, 3],
        shouldLoopPlaybackRange: loop,
        autoLoop: loop,
      })
      const { result, unmount } = renderHook(() => useAnnotationWorkspaceVideoSync(options))
      const video = document.createElement('video')
      Object.defineProperty(video, 'duration', { value: 10, configurable: true })
      Object.defineProperty(video, 'currentTime', { value: 3, configurable: true, writable: true })
      Object.defineProperty(video, 'paused', { value: false, configurable: true })
      video.pause = vi.fn()
      video.play = vi.fn(() => Promise.resolve())
      result.current.videoRef.current = video
      act(() => vi.advanceTimersByTime(20))
      if (loop) {
        expect(video.currentTime).toBeLessThan(3)
        expect(options.onTogglePlayback).not.toHaveBeenCalled()
      } else {
        expect(video.pause).toHaveBeenCalledOnce()
        expect(options.onTogglePlayback).toHaveBeenCalledOnce()
      }
      unmount()
    },
  )

  it('recovers paused media while playback is active and cleans up its animation work', () => {
    const options = playbackOptions({ isPlaying: true, videoSrc: '/source.mp4' })
    const { result, unmount } = renderHook(() => useAnnotationWorkspaceVideoSync(options))
    const video = document.createElement('video')
    Object.defineProperty(video, 'currentTime', { value: 0.2, configurable: true, writable: true })
    Object.defineProperty(video, 'duration', { value: 1, configurable: true })
    Object.defineProperty(video, 'paused', { value: true, configurable: true })
    video.pause = vi.fn()
    video.play = vi.fn(() => Promise.resolve())
    result.current.videoRef.current = video
    act(() => vi.advanceTimersByTime(350))
    expect(options.onRecordEvent).toHaveBeenCalledWith(
      'playback',
      'desync-recover',
      expect.objectContaining({ currentFrame: 2 }),
    )
    expect(video.play).toHaveBeenCalled()
    unmount()
    vi.mocked(options.onSetCurrentFrame).mockClear()
    act(() => vi.advanceTimersByTime(100))
    expect(options.onSetCurrentFrame).not.toHaveBeenCalled()
  })

  it.each([false, true])('only requests metadata autoplay while visible (hidden=%s)', (hidden) => {
    const visibility = vi.spyOn(document, 'hidden', 'get').mockReturnValue(hidden)
    const togglePlayback = vi.fn()
    const baseProps = {
      currentFrame: 0,
      totalFrames: 12,
      originalFrameIndex: 0,
      activePlaybackRange: null as [number, number] | null,
      playbackRangeStart: 0,
      playbackRangeEnd: 11,
      isPlaying: false,
      playbackSpeed: 1,
      autoPlay: true,
      autoLoop: false,
      shouldLoopPlaybackRange: false,
      datasetFps: 24,
      insertedFrames: new Map<number, FrameInsertion>(),
      removedFrames: new Set<number>(),
      videoSrc: '/videos/wrist.mp4',
      onSetCurrentFrame: vi.fn(),
      onTogglePlayback: togglePlayback,
      onSetFrameWithinPlaybackRange: vi.fn(),
      onRecordEvent: vi.fn(),
    }

    const { result } = renderHook((props) => useAnnotationWorkspaceVideoSync(props), {
      initialProps: baseProps,
    })

    const video = document.createElement('video')
    Object.defineProperty(video, 'duration', { configurable: true, value: 8.4 })

    act(() => {
      result.current.handleLoadedMetadata({
        currentTarget: video,
      } as SyntheticEvent<HTMLVideoElement>)
    })
    expect(togglePlayback).toHaveBeenCalledTimes(hidden ? 0 : 1)
    visibility.mockRestore()
  })

  it('re-arms autoplay when videoSrc changes between episodes', () => {
    const togglePlayback = vi.fn()
    const baseProps = {
      currentFrame: 0,
      totalFrames: 12,
      originalFrameIndex: 0,
      activePlaybackRange: null as [number, number] | null,
      playbackRangeStart: 0,
      playbackRangeEnd: 11,
      isPlaying: false,
      playbackSpeed: 1,
      autoPlay: true,
      autoLoop: false,
      shouldLoopPlaybackRange: false,
      datasetFps: 24,
      insertedFrames: new Map<number, FrameInsertion>(),
      removedFrames: new Set<number>(),
      videoSrc: '/videos/episode-0.mp4',
      onSetCurrentFrame: vi.fn(),
      onTogglePlayback: togglePlayback,
      onSetFrameWithinPlaybackRange: vi.fn(),
      onRecordEvent: vi.fn(),
    }

    const { result, rerender } = renderHook((props) => useAnnotationWorkspaceVideoSync(props), {
      initialProps: baseProps,
    })

    const video = document.createElement('video')
    Object.defineProperty(video, 'duration', { configurable: true, value: 8.4 })

    // Episode 0: metadata loads → autoplay fires immediately
    act(() => {
      result.current.handleLoadedMetadata({
        currentTarget: video,
      } as SyntheticEvent<HTMLVideoElement>)
    })
    expect(togglePlayback).toHaveBeenCalledTimes(1)

    // Episode 1: switch episode, metadata loads → autoplay fires again
    rerender({ ...baseProps, videoSrc: '/videos/episode-1.mp4', isPlaying: true })
    rerender({ ...baseProps, videoSrc: '/videos/episode-1.mp4', isPlaying: false })
    act(() => {
      result.current.handleLoadedMetadata({
        currentTarget: video,
      } as SyntheticEvent<HTMLVideoElement>)
    })
    expect(togglePlayback).toHaveBeenCalledTimes(2)
  })

  it('re-arms autoplay when totalFrames changes between frame-only episodes', () => {
    const togglePlayback = vi.fn()
    const baseProps = {
      currentFrame: 0,
      totalFrames: 100,
      originalFrameIndex: 0,
      activePlaybackRange: null as [number, number] | null,
      playbackRangeStart: 0,
      playbackRangeEnd: 99,
      isPlaying: false,
      playbackSpeed: 1,
      autoPlay: true,
      autoLoop: false,
      shouldLoopPlaybackRange: false,
      datasetFps: 30,
      insertedFrames: new Map<number, FrameInsertion>(),
      removedFrames: new Set<number>(),
      videoSrc: null,
      onSetCurrentFrame: vi.fn(),
      onTogglePlayback: togglePlayback,
      onSetFrameWithinPlaybackRange: vi.fn(),
      onRecordEvent: vi.fn(),
    }

    const { rerender } = renderHook((props) => useAnnotationWorkspaceVideoSync(props), {
      initialProps: baseProps,
    })

    expect(togglePlayback).toHaveBeenCalledTimes(1)

    rerender({ ...baseProps, isPlaying: true })
    rerender({ ...baseProps, totalFrames: 185, playbackRangeEnd: 184, isPlaying: false })

    expect(togglePlayback).toHaveBeenCalledTimes(2)

    rerender({ ...baseProps, totalFrames: 185, playbackRangeEnd: 184, isPlaying: true })
    rerender({ ...baseProps, totalFrames: 118, playbackRangeEnd: 117, isPlaying: false })

    expect(togglePlayback).toHaveBeenCalledTimes(3)
  })

  it('pauses the video element when isPlaying transitions to false after metadata load during playback', () => {
    const baseProps = {
      currentFrame: 0,
      totalFrames: 300,
      originalFrameIndex: 0,
      activePlaybackRange: null as [number, number] | null,
      playbackRangeStart: 0,
      playbackRangeEnd: 299,
      isPlaying: false,
      playbackSpeed: 1,
      autoPlay: false,
      autoLoop: false,
      shouldLoopPlaybackRange: false,
      datasetFps: 30,
      insertedFrames: new Map<number, FrameInsertion>(),
      removedFrames: new Set<number>(),
      videoSrc: '/videos/episode-0.mp4',
      onSetCurrentFrame: vi.fn(),
      onTogglePlayback: vi.fn(),
      onSetFrameWithinPlaybackRange: vi.fn(),
      onRecordEvent: vi.fn(),
    }

    const video = document.createElement('video')
    Object.defineProperty(video, 'duration', { configurable: true, value: 10 })
    Object.defineProperty(video, 'currentTime', { configurable: true, writable: true, value: 0 })
    const pauseMock = vi.fn()
    video.pause = pauseMock
    video.play = vi.fn(() => Promise.resolve())

    const { result, rerender } = renderHook((props) => useAnnotationWorkspaceVideoSync(props), {
      initialProps: baseProps,
    })

    act(() => {
      Object.defineProperty(result.current.videoRef, 'current', { value: video, writable: true })
    })

    // First metadata load: sets videoDuration from 0 → 10, consuming any future skip flag
    act(() => {
      result.current.handleLoadedMetadata({
        currentTarget: video,
      } as SyntheticEvent<HTMLVideoElement>)
    })

    // Start playback
    rerender({ ...baseProps, isPlaying: true })

    // Second metadata load while playing: sets skipNextPlaybackSync = true
    // Since videoDuration is already 10, setVideoDuration(10) won't re-render to consume it
    act(() => {
      result.current.handleLoadedMetadata({
        currentTarget: video,
      } as SyntheticEvent<HTMLVideoElement>)
    })

    // Clear all pause() calls from ensureVideoPlaybackAtTime during setup
    pauseMock.mockClear()

    // Pause playback — the video element must be paused
    rerender({ ...baseProps, isPlaying: false })

    expect(pauseMock).toHaveBeenCalled()
  })

  it('jumps to the playback range start when the video ends in loop mode', () => {
    const setFrameWithinPlaybackRange = vi.fn()
    const { result } = renderHook(() =>
      useAnnotationWorkspaceVideoSync({
        currentFrame: 8,
        totalFrames: 12,
        originalFrameIndex: 8,
        activePlaybackRange: [3, 9],
        playbackRangeStart: 3,
        playbackRangeEnd: 9,
        isPlaying: true,
        playbackSpeed: 1,
        autoPlay: false,
        autoLoop: true,
        shouldLoopPlaybackRange: true,
        datasetFps: 24,
        insertedFrames: new Map(),
        removedFrames: new Set(),
        videoSrc: '/videos/wrist.mp4',
        onSetCurrentFrame: vi.fn(),
        onTogglePlayback: vi.fn(),
        onSetFrameWithinPlaybackRange: setFrameWithinPlaybackRange,
        onRecordEvent: vi.fn(),
      }),
    )

    act(() => {
      result.current.handleVideoEnded()
    })

    expect(setFrameWithinPlaybackRange).toHaveBeenCalledWith(3)
  })

  it('applies playback rate directly when speed changes during video playback without pause/seek', () => {
    const baseProps = {
      currentFrame: 50,
      totalFrames: 300,
      originalFrameIndex: 50,
      activePlaybackRange: null as [number, number] | null,
      playbackRangeStart: 0,
      playbackRangeEnd: 299,
      isPlaying: true,
      playbackSpeed: 1,
      autoPlay: false,
      autoLoop: false,
      shouldLoopPlaybackRange: false,
      datasetFps: 30,
      insertedFrames: new Map<number, FrameInsertion>(),
      removedFrames: new Set<number>(),
      videoSrc: '/videos/episode-0.mp4',
      onSetCurrentFrame: vi.fn(),
      onTogglePlayback: vi.fn(),
      onSetFrameWithinPlaybackRange: vi.fn(),
      onRecordEvent: vi.fn(),
    }

    const video = document.createElement('video')
    Object.defineProperty(video, 'duration', { configurable: true, value: 10 })
    Object.defineProperty(video, 'currentTime', { configurable: true, writable: true, value: 1.5 })
    const pauseMock = vi.fn()
    video.pause = pauseMock
    video.play = vi.fn(() => Promise.resolve())

    const { result, rerender } = renderHook((props) => useAnnotationWorkspaceVideoSync(props), {
      initialProps: baseProps,
    })

    act(() => {
      Object.defineProperty(result.current.videoRef, 'current', { value: video, writable: true })
    })

    // Initial sync — fires the full sync dance (pause/seek/play)
    act(() => {
      result.current.handleLoadedMetadata({
        currentTarget: video,
      } as SyntheticEvent<HTMLVideoElement>)
    })

    // Clear setup calls
    pauseMock.mockClear()
    ;(video.play as ReturnType<typeof vi.fn>).mockClear()

    // Change speed from 1x to 2x — should NOT cause pause/seek/play cycle
    rerender({ ...baseProps, playbackSpeed: 2 })

    // The video.playbackRate should be updated directly
    expect(video.playbackRate).toBe(2)
    // Should NOT have called pause (no sync dance)
    expect(pauseMock).not.toHaveBeenCalled()
  })

  it('does not call pause/play during loop restarts in the RAF tick', () => {
    const setCurrentFrame = vi.fn()
    const baseProps = {
      currentFrame: 0,
      totalFrames: 300,
      originalFrameIndex: 0,
      activePlaybackRange: [50, 100] as [number, number],
      playbackRangeStart: 50,
      playbackRangeEnd: 100,
      isPlaying: true,
      playbackSpeed: 5,
      autoPlay: false,
      autoLoop: true,
      shouldLoopPlaybackRange: true,
      datasetFps: 30,
      insertedFrames: new Map<number, FrameInsertion>(),
      removedFrames: new Set<number>(),
      videoSrc: '/videos/episode-0.mp4',
      onSetCurrentFrame: setCurrentFrame,
      onTogglePlayback: vi.fn(),
      onSetFrameWithinPlaybackRange: vi.fn(),
      onRecordEvent: vi.fn(),
    }

    const video = document.createElement('video')
    Object.defineProperty(video, 'duration', { configurable: true, value: 10 })
    Object.defineProperty(video, 'currentTime', {
      configurable: true,
      writable: true,
      value: 50 / 30,
    })
    Object.defineProperty(video, 'paused', { configurable: true, writable: true, value: false })
    const pauseMock = vi.fn()
    video.pause = pauseMock
    video.play = vi.fn(() => Promise.resolve())

    const { result } = renderHook((props) => useAnnotationWorkspaceVideoSync(props), {
      initialProps: baseProps,
    })

    act(() => {
      Object.defineProperty(result.current.videoRef, 'current', { value: video, writable: true })
    })

    // Clear all setup calls
    pauseMock.mockClear()
    ;(video.play as ReturnType<typeof vi.fn>).mockClear()

    // Simulate the video reaching past range end (loop restart needed)
    act(() => {
      video.currentTime = 101 / 30
      vi.advanceTimersByTime(50)
    })

    // During loop restart, the video should NOT be paused then played (no sync dance).
    // It should just seek directly via currentTime.
    const playCallsAfterClear = (video.play as ReturnType<typeof vi.fn>).mock.calls.length
    const pauseCallsAfterClear = pauseMock.mock.calls.length

    // The loop restart should use lightweight seek (no pause/play calls)
    expect(pauseCallsAfterClear).toBe(0)
    expect(playCallsAfterClear).toBe(0)
  })

  it('stops RAF processing immediately when effect deps change (disposed flag)', () => {
    const setCurrentFrame = vi.fn()
    const togglePlayback = vi.fn()
    const baseProps = {
      currentFrame: 50,
      totalFrames: 300,
      originalFrameIndex: 50,
      activePlaybackRange: [50, 100] as [number, number],
      playbackRangeStart: 50,
      playbackRangeEnd: 100,
      isPlaying: true,
      playbackSpeed: 1,
      autoPlay: false,
      autoLoop: true,
      shouldLoopPlaybackRange: true,
      datasetFps: 30,
      insertedFrames: new Map<number, FrameInsertion>(),
      removedFrames: new Set<number>(),
      videoSrc: '/videos/episode-0.mp4',
      onSetCurrentFrame: setCurrentFrame,
      onTogglePlayback: togglePlayback,
      onSetFrameWithinPlaybackRange: vi.fn(),
      onRecordEvent: vi.fn(),
    }

    const video = document.createElement('video')
    Object.defineProperty(video, 'duration', { configurable: true, value: 10 })
    Object.defineProperty(video, 'currentTime', {
      configurable: true,
      writable: true,
      value: 100 / 30,
    })
    video.pause = vi.fn()
    video.play = vi.fn(() => Promise.resolve())

    const { result, rerender } = renderHook((props) => useAnnotationWorkspaceVideoSync(props), {
      initialProps: baseProps,
    })

    act(() => {
      Object.defineProperty(result.current.videoRef, 'current', { value: video, writable: true })
    })

    // Turn off auto-loop — effect should restart with new shouldLoopPlaybackRange
    setCurrentFrame.mockClear()
    rerender({
      ...baseProps,
      autoLoop: false,
      shouldLoopPlaybackRange: false,
    })

    // Simulate video at range end — with loop off, should NOT wrap to start
    act(() => {
      video.currentTime = 101 / 30
      vi.advanceTimersByTime(50)
    })

    // With autoLoop disabled, reaching past the range end should stop playback,
    // not loop back to 50
    const frames = setCurrentFrame.mock.calls.map((c) => c[0] as number)
    const hasLoopBack = frames.some((f: number) => f === 50)
    expect(hasLoopBack).toBe(false)
  })

  it('does not set retry timers in ensureVideoPlaybackAtTime', () => {
    const baseProps = {
      currentFrame: 0,
      totalFrames: 300,
      originalFrameIndex: 0,
      activePlaybackRange: null as [number, number] | null,
      playbackRangeStart: 0,
      playbackRangeEnd: 299,
      isPlaying: true,
      playbackSpeed: 1,
      autoPlay: false,
      autoLoop: false,
      shouldLoopPlaybackRange: false,
      datasetFps: 30,
      insertedFrames: new Map<number, FrameInsertion>(),
      removedFrames: new Set<number>(),
      videoSrc: '/videos/episode-0.mp4',
      onSetCurrentFrame: vi.fn(),
      onTogglePlayback: vi.fn(),
      onSetFrameWithinPlaybackRange: vi.fn(),
      onRecordEvent: vi.fn(),
    }

    const video = document.createElement('video')
    Object.defineProperty(video, 'duration', { configurable: true, value: 10 })
    Object.defineProperty(video, 'currentTime', { configurable: true, writable: true, value: 0 })
    video.pause = vi.fn()
    video.play = vi.fn(() => Promise.resolve())

    const { result } = renderHook((props) => useAnnotationWorkspaceVideoSync(props), {
      initialProps: baseProps,
    })

    act(() => {
      Object.defineProperty(result.current.videoRef, 'current', { value: video, writable: true })
    })

    // The initial sync calls ensureVideoPlaybackAtTime which should NOT set timeouts.
    // Count active timers — with retry removed, no timers should be pending.
    const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout')
    const callsBefore = setTimeoutSpy.mock.calls.length

    // Trigger a sync that calls ensureVideoPlaybackAtTime
    act(() => {
      result.current.handleLoadedMetadata({
        currentTarget: video,
      } as SyntheticEvent<HTMLVideoElement>)
    })

    // No new setTimeout calls should have been made by ensureVideoPlaybackAtTime
    const timeoutCalls = setTimeoutSpy.mock.calls.slice(callsBefore)
    const retryTimeouts = timeoutCalls.filter(([, delay]) => delay === 180)
    expect(retryTimeouts).toHaveLength(0)
    setTimeoutSpy.mockRestore()
  })

  it('does not re-sync video when callback deps change identity during playback', () => {
    const baseProps = {
      currentFrame: 50,
      totalFrames: 300,
      originalFrameIndex: 50,
      activePlaybackRange: null as [number, number] | null,
      playbackRangeStart: 0,
      playbackRangeEnd: 299,
      isPlaying: true,
      playbackSpeed: 1,
      autoPlay: false,
      autoLoop: false,
      shouldLoopPlaybackRange: false,
      datasetFps: 30,
      insertedFrames: new Map<number, FrameInsertion>(),
      removedFrames: new Set<number>(),
      videoSrc: '/videos/episode-0.mp4',
      onSetCurrentFrame: vi.fn(),
      onTogglePlayback: vi.fn(),
      onSetFrameWithinPlaybackRange: vi.fn(),
      onRecordEvent: vi.fn(),
    }

    const video = document.createElement('video')
    Object.defineProperty(video, 'duration', { configurable: true, value: 10 })
    Object.defineProperty(video, 'currentTime', {
      configurable: true,
      writable: true,
      value: 50 / 30,
    })
    Object.defineProperty(video, 'paused', { configurable: true, writable: true, value: false })
    const pauseMock = vi.fn()
    video.pause = pauseMock
    video.play = vi.fn(() => Promise.resolve())

    const { result, rerender } = renderHook((props) => useAnnotationWorkspaceVideoSync(props), {
      initialProps: baseProps,
    })

    act(() => {
      Object.defineProperty(result.current.videoRef, 'current', { value: video, writable: true })
    })

    act(() => {
      result.current.handleLoadedMetadata({
        currentTarget: video,
      } as SyntheticEvent<HTMLVideoElement>)
    })

    pauseMock.mockClear()

    // Simulate re-renders with unstable callback references (new function each time).
    // This mimics the real scenario where onSetFrameWithinPlaybackRange changes identity
    // on every render because its parent passes an inline arrow function.
    for (let i = 0; i < 5; i++) {
      rerender({
        ...baseProps,
        onSetFrameWithinPlaybackRange: vi.fn(),
      })
    }

    // The sync effect should NOT re-fire during normal playback just because a callback
    // dep changed identity. Only isPlaying or videoSrc changes should trigger re-sync.
    expect(pauseMock).not.toHaveBeenCalled()
  })
})

describe('useAnnotationWorkspaceVideoSync videoWindow', () => {
  it('seeks into the window start on loadedmetadata when frame is at episode start', () => {
    const baseProps = {
      currentFrame: 0,
      totalFrames: 10,
      originalFrameIndex: 0,
      activePlaybackRange: null as [number, number] | null,
      playbackRangeStart: 0,
      playbackRangeEnd: 9,
      isPlaying: false,
      playbackSpeed: 1,
      autoPlay: false,
      autoLoop: false,
      shouldLoopPlaybackRange: false,
      datasetFps: 30,
      insertedFrames: new Map<number, FrameInsertion>(),
      removedFrames: new Set<number>(),
      videoSrc: '/videos/concat.mp4',
      videoWindow: [2.0, 5.0] as [number, number],
      onSetCurrentFrame: vi.fn(),
      onTogglePlayback: vi.fn(),
      onSetFrameWithinPlaybackRange: vi.fn(),
      onRecordEvent: vi.fn(),
    }

    const { result } = renderHook((props) => useAnnotationWorkspaceVideoSync(props), {
      initialProps: baseProps,
    })

    const video = document.createElement('video')
    Object.defineProperty(video, 'duration', { configurable: true, value: 60 })
    Object.defineProperty(video, 'currentTime', { configurable: true, writable: true, value: 0 })

    act(() => {
      result.current.handleLoadedMetadata({
        currentTarget: video,
      } as SyntheticEvent<HTMLVideoElement>)
    })

    expect(video.currentTime).toBeCloseTo(2.0, 5)
  })

  it.each([30, 20])(
    'preserves the source window across camera switches with %i timeline frames',
    (totalFrames) => {
      // totalFrames=30, windowDuration=3s → fps=10. Frame 6 → 0.6s episode time
      // → toVideoTime(0.6) = 2.0 + 0.6 = 2.6
      const baseProps = {
        currentFrame: 6,
        totalFrames,
        sourceFrameCount: 30,
        originalFrameIndex: 6,
        activePlaybackRange: null as [number, number] | null,
        playbackRangeStart: 0,
        playbackRangeEnd: totalFrames - 1,
        isPlaying: false,
        playbackSpeed: 1,
        autoPlay: false,
        autoLoop: false,
        shouldLoopPlaybackRange: false,
        datasetFps: 30,
        insertedFrames: new Map<number, FrameInsertion>(),
        removedFrames: new Set<number>(),
        videoSrc: '/videos/concat.mp4',
        videoWindow: [2.0, 5.0] as [number, number],
        onSetCurrentFrame: vi.fn(),
        onTogglePlayback: vi.fn(),
        onSetFrameWithinPlaybackRange: vi.fn(),
        onRecordEvent: vi.fn(),
      }

      const { result, rerender } = renderHook((props) => useAnnotationWorkspaceVideoSync(props), {
        initialProps: baseProps,
      })

      const video = document.createElement('video')
      Object.defineProperty(video, 'duration', { configurable: true, value: 60 })
      Object.defineProperty(video, 'currentTime', { configurable: true, writable: true, value: 0 })

      act(() => {
        result.current.handleLoadedMetadata({
          currentTarget: video,
        } as SyntheticEvent<HTMLVideoElement>)
      })

      expect(video.currentTime).toBeCloseTo(2.6, 5)

      const toolVideo = document.createElement('video')
      Object.defineProperty(toolVideo, 'duration', { configurable: true, value: 60 })
      Object.defineProperty(toolVideo, 'currentTime', {
        configurable: true,
        writable: true,
        value: 0,
      })
      act(() => {
        Object.defineProperty(result.current.videoRef, 'current', {
          value: toolVideo,
          writable: true,
        })
      })

      const toolProps = { ...baseProps, videoSrc: '/videos/tool.mp4' }
      rerender(toolProps)
      expect(toolVideo.currentTime).toBeCloseTo(2.6, 5)

      const advancedProps = { ...toolProps, currentFrame: 9, originalFrameIndex: 9 }
      rerender(advancedProps)
      expect(toolVideo.currentTime).toBeCloseTo(2.9, 5)

      act(() => {
        Object.defineProperty(result.current.videoRef, 'current', { value: video, writable: true })
      })
      rerender({ ...advancedProps, videoSrc: baseProps.videoSrc })
      expect(video.currentTime).toBeCloseTo(2.9, 5)
    },
  )

  it('seeks to frame/fps without offset when videoWindow is null', () => {
    // No window → videoOffset=0. videoDuration is updated via useState during the
    // metadata callback so fps stays at datasetFps on this first sync pass.
    // Frame 15 → toVideoTime(15/30) = 0.5.
    const baseProps = {
      currentFrame: 15,
      totalFrames: 30,
      originalFrameIndex: 15,
      activePlaybackRange: null as [number, number] | null,
      playbackRangeStart: 0,
      playbackRangeEnd: 29,
      isPlaying: false,
      playbackSpeed: 1,
      autoPlay: false,
      autoLoop: false,
      shouldLoopPlaybackRange: false,
      datasetFps: 30,
      insertedFrames: new Map<number, FrameInsertion>(),
      removedFrames: new Set<number>(),
      videoSrc: '/videos/single.mp4',
      videoWindow: null as [number, number] | null,
      onSetCurrentFrame: vi.fn(),
      onTogglePlayback: vi.fn(),
      onSetFrameWithinPlaybackRange: vi.fn(),
      onRecordEvent: vi.fn(),
    }

    const { result } = renderHook((props) => useAnnotationWorkspaceVideoSync(props), {
      initialProps: baseProps,
    })

    const video = document.createElement('video')
    Object.defineProperty(video, 'duration', { configurable: true, value: 10 })
    Object.defineProperty(video, 'currentTime', { configurable: true, writable: true, value: 0 })

    act(() => {
      result.current.handleLoadedMetadata({
        currentTarget: video,
      } as SyntheticEvent<HTMLVideoElement>)
    })

    expect(video.currentTime).toBeCloseTo(0.5, 5)
  })

  it('translates frame seeks into absolute video time with the window offset', () => {
    // Frame seeks use the same offset. We confirm via seekVideoFrame() that the
    // requested frame maps to videoOffset + (frame / fps).
    const baseProps = {
      currentFrame: 0,
      totalFrames: 30,
      originalFrameIndex: 0,
      activePlaybackRange: null as [number, number] | null,
      playbackRangeStart: 0,
      playbackRangeEnd: 29,
      isPlaying: false,
      playbackSpeed: 1,
      autoPlay: false,
      autoLoop: false,
      shouldLoopPlaybackRange: false,
      datasetFps: 30,
      insertedFrames: new Map<number, FrameInsertion>(),
      removedFrames: new Set<number>(),
      videoSrc: '/videos/concat.mp4',
      videoWindow: [2.0, 5.0] as [number, number],
      onSetCurrentFrame: vi.fn(),
      onTogglePlayback: vi.fn(),
      onSetFrameWithinPlaybackRange: vi.fn(),
      onRecordEvent: vi.fn(),
    }

    const video = document.createElement('video')
    Object.defineProperty(video, 'duration', { configurable: true, value: 60 })
    Object.defineProperty(video, 'currentTime', { configurable: true, writable: true, value: 2.0 })

    const { result } = renderHook((props) => useAnnotationWorkspaceVideoSync(props), {
      initialProps: baseProps,
    })

    act(() => {
      Object.defineProperty(result.current.videoRef, 'current', { value: video, writable: true })
    })

    act(() => {
      result.current.seekVideoFrame(9, null, true)
    })

    // fps = 30 / 3 = 10. Frame 9 → 0.9s episode time → 2.9s absolute.
    expect(video.currentTime).toBeCloseTo(2.9, 5)
  })
})
