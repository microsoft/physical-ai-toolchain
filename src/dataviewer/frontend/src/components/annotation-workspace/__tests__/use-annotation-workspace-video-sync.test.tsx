import { act, renderHook } from '@testing-library/react'
import type { SyntheticEvent } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAnnotationWorkspaceVideoSync } from '@/components/annotation-workspace/useAnnotationWorkspaceVideoSync'
import type { FrameInsertion } from '@/types/episode-edit'

describe('useAnnotationWorkspaceVideoSync', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  it('requests playback after metadata loads when autoplay is armed', () => {
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
      frameCacheReady: false,
      onSetCurrentFrame: vi.fn(),
      onTogglePlayback: togglePlayback,
      onSetFrameWithinPlaybackRange: vi.fn(),
      onRecordEvent: vi.fn(),
    }

    const { result } = renderHook(
      (props) => useAnnotationWorkspaceVideoSync(props),
      { initialProps: baseProps },
    )

    const video = document.createElement('video')
    Object.defineProperty(video, 'duration', { configurable: true, value: 8.4 })

    act(() => {
      result.current.handleLoadedMetadata({ currentTarget: video } as SyntheticEvent<HTMLVideoElement>)
    })
    expect(togglePlayback).toHaveBeenCalledTimes(1)
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
      frameCacheReady: false,
      onSetCurrentFrame: vi.fn(),
      onTogglePlayback: togglePlayback,
      onSetFrameWithinPlaybackRange: vi.fn(),
      onRecordEvent: vi.fn(),
    }

    const { result, rerender } = renderHook(
      (props) => useAnnotationWorkspaceVideoSync(props),
      { initialProps: baseProps },
    )

    const video = document.createElement('video')
    Object.defineProperty(video, 'duration', { configurable: true, value: 8.4 })

    // Episode 0: metadata loads → autoplay fires immediately
    act(() => {
      result.current.handleLoadedMetadata({ currentTarget: video } as SyntheticEvent<HTMLVideoElement>)
    })
    expect(togglePlayback).toHaveBeenCalledTimes(1)

    // Episode 1: switch episode, metadata loads → autoplay fires again
    rerender({ ...baseProps, videoSrc: '/videos/episode-1.mp4', isPlaying: true })
    rerender({ ...baseProps, videoSrc: '/videos/episode-1.mp4', isPlaying: false })
    act(() => {
      result.current.handleLoadedMetadata({ currentTarget: video } as SyntheticEvent<HTMLVideoElement>)
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

    const { rerender } = renderHook(
      (props) => useAnnotationWorkspaceVideoSync(props),
      { initialProps: baseProps },
    )

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

    const { result, rerender } = renderHook(
      (props) => useAnnotationWorkspaceVideoSync(props),
      { initialProps: baseProps },
    )

    act(() => {
      Object.defineProperty(result.current.videoRef, 'current', { value: video, writable: true })
    })

    // First metadata load: sets videoDuration from 0 → 10, consuming any future skip flag
    act(() => {
      result.current.handleLoadedMetadata({ currentTarget: video } as SyntheticEvent<HTMLVideoElement>)
    })

    // Start playback
    rerender({ ...baseProps, isPlaying: true })

    // Second metadata load while playing: sets skipNextPlaybackSync = true
    // Since videoDuration is already 10, setVideoDuration(10) won't re-render to consume it
    act(() => {
      result.current.handleLoadedMetadata({ currentTarget: video } as SyntheticEvent<HTMLVideoElement>)
    })

    // Clear all pause() calls from ensureVideoPlaybackAtTime during setup
    pauseMock.mockClear()

    // Pause playback — the video element must be paused
    rerender({ ...baseProps, isPlaying: false })

    expect(pauseMock).toHaveBeenCalled()
  })

  it('jumps to the playback range start when the video ends in loop mode', () => {
    const setFrameWithinPlaybackRange = vi.fn()
    const { result } = renderHook(() => useAnnotationWorkspaceVideoSync({
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
    }))

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

    const { result, rerender } = renderHook(
      (props) => useAnnotationWorkspaceVideoSync(props),
      { initialProps: baseProps },
    )

    act(() => {
      Object.defineProperty(result.current.videoRef, 'current', { value: video, writable: true })
    })

    // Initial sync — fires the full sync dance (pause/seek/play)
    act(() => {
      result.current.handleLoadedMetadata({ currentTarget: video } as SyntheticEvent<HTMLVideoElement>)
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
    Object.defineProperty(video, 'currentTime', { configurable: true, writable: true, value: 50 / 30 })
    Object.defineProperty(video, 'paused', { configurable: true, writable: true, value: false })
    const pauseMock = vi.fn()
    video.pause = pauseMock
    video.play = vi.fn(() => Promise.resolve())

    const { result } = renderHook(
      (props) => useAnnotationWorkspaceVideoSync(props),
      { initialProps: baseProps },
    )

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
    Object.defineProperty(video, 'currentTime', { configurable: true, writable: true, value: 100 / 30 })
    video.pause = vi.fn()
    video.play = vi.fn(() => Promise.resolve())

    const { result, rerender } = renderHook(
      (props) => useAnnotationWorkspaceVideoSync(props),
      { initialProps: baseProps },
    )

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

    const { result } = renderHook(
      (props) => useAnnotationWorkspaceVideoSync(props),
      { initialProps: baseProps },
    )

    act(() => {
      Object.defineProperty(result.current.videoRef, 'current', { value: video, writable: true })
    })

    // The initial sync calls ensureVideoPlaybackAtTime which should NOT set timeouts.
    // Count active timers — with retry removed, no timers should be pending.
    const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout')
    const callsBefore = setTimeoutSpy.mock.calls.length

    // Trigger a sync that calls ensureVideoPlaybackAtTime
    act(() => {
      result.current.handleLoadedMetadata({ currentTarget: video } as SyntheticEvent<HTMLVideoElement>)
    })

    // No new setTimeout calls should have been made by ensureVideoPlaybackAtTime
    const timeoutCalls = setTimeoutSpy.mock.calls.slice(callsBefore)
    const retryTimeouts = timeoutCalls.filter(([, delay]) => delay === 180)
    expect(retryTimeouts).toHaveLength(0)
    setTimeoutSpy.mockRestore()
  })

  it('returns a displayCanvasRef for canvas-based video rendering', () => {
    const { result } = renderHook(() => useAnnotationWorkspaceVideoSync({
      currentFrame: 0,
      totalFrames: 12,
      originalFrameIndex: 0,
      activePlaybackRange: null,
      playbackRangeStart: 0,
      playbackRangeEnd: 11,
      isPlaying: false,
      playbackSpeed: 1,
      autoPlay: false,
      autoLoop: false,
      shouldLoopPlaybackRange: false,
      datasetFps: 30,
      insertedFrames: new Map(),
      removedFrames: new Set(),
      videoSrc: '/videos/test.mp4',
      onSetCurrentFrame: vi.fn(),
      onTogglePlayback: vi.fn(),
      onSetFrameWithinPlaybackRange: vi.fn(),
      onRecordEvent: vi.fn(),
    }))

    expect(result.current.displayCanvasRef).toBeDefined()
    expect(result.current.displayCanvasRef.current).toBeNull()
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
    Object.defineProperty(video, 'currentTime', { configurable: true, writable: true, value: 50 / 30 })
    Object.defineProperty(video, 'paused', { configurable: true, writable: true, value: false })
    const pauseMock = vi.fn()
    video.pause = pauseMock
    video.play = vi.fn(() => Promise.resolve())

    const { result, rerender } = renderHook(
      (props) => useAnnotationWorkspaceVideoSync(props),
      { initialProps: baseProps },
    )

    act(() => {
      Object.defineProperty(result.current.videoRef, 'current', { value: video, writable: true })
    })

    act(() => {
      result.current.handleLoadedMetadata({ currentTarget: video } as SyntheticEvent<HTMLVideoElement>)
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

  it('draws from frame cache instead of video when cache is ready and paused', () => {
    const drawImageSpy = vi.fn()
    const canvas = document.createElement('canvas')
    vi.spyOn(canvas, 'getContext').mockReturnValue({ drawImage: drawImageSpy } as unknown as CanvasRenderingContext2D)

    const closeFn = vi.fn()
    const mockBitmap = { close: closeFn, width: 640, height: 480 } as unknown as ImageBitmap
    const frameCache = new Map<number, ImageBitmap>()
    frameCache.set(10, mockBitmap)
    frameCache.set(11, mockBitmap)

    const video = document.createElement('video')
    Object.defineProperty(video, 'duration', { configurable: true, value: 10 })
    Object.defineProperty(video, 'currentTime', { configurable: true, writable: true, value: 0 })
    Object.defineProperty(video, 'videoWidth', { configurable: true, value: 640 })
    Object.defineProperty(video, 'videoHeight', { configurable: true, value: 480 })
    video.pause = vi.fn()
    video.play = vi.fn(() => Promise.resolve())

    const baseProps = {
      currentFrame: 10,
      totalFrames: 100,
      originalFrameIndex: 10,
      activePlaybackRange: null as [number, number] | null,
      playbackRangeStart: 0,
      playbackRangeEnd: 99,
      isPlaying: false,
      playbackSpeed: 1,
      autoPlay: false,
      autoLoop: false,
      shouldLoopPlaybackRange: false,
      datasetFps: 30,
      insertedFrames: new Map<number, FrameInsertion>(),
      removedFrames: new Set<number>(),
      videoSrc: '/videos/test.mp4',
      frameCache,
      frameCacheReady: true,
      onSetCurrentFrame: vi.fn(),
      onTogglePlayback: vi.fn(),
      onSetFrameWithinPlaybackRange: vi.fn(),
      onRecordEvent: vi.fn(),
    }

    const { result, rerender } = renderHook(
      (props) => useAnnotationWorkspaceVideoSync(props),
      { initialProps: baseProps },
    )

    act(() => {
      Object.defineProperty(result.current.videoRef, 'current', { value: video, writable: true })
      Object.defineProperty(result.current.displayCanvasRef, 'current', { value: canvas, writable: true })
    })

    drawImageSpy.mockClear()

    // Change frame — should draw from cache, not seek the video
    rerender({ ...baseProps, currentFrame: 11, originalFrameIndex: 11 })

    expect(drawImageSpy).toHaveBeenCalledWith(mockBitmap, 0, 0)
  })

  it('draws to display canvas on seeked event when paused', () => {
    const baseProps = {
      currentFrame: 0,
      totalFrames: 100,
      originalFrameIndex: 0,
      activePlaybackRange: null as [number, number] | null,
      playbackRangeStart: 0,
      playbackRangeEnd: 99,
      isPlaying: false,
      playbackSpeed: 1,
      autoPlay: false,
      autoLoop: false,
      shouldLoopPlaybackRange: false,
      datasetFps: 30,
      insertedFrames: new Map<number, FrameInsertion>(),
      removedFrames: new Set<number>(),
      videoSrc: null as string | null,
      onSetCurrentFrame: vi.fn(),
      onTogglePlayback: vi.fn(),
      onSetFrameWithinPlaybackRange: vi.fn(),
      onRecordEvent: vi.fn(),
    }

    const video = document.createElement('video')
    Object.defineProperty(video, 'duration', { configurable: true, value: 10 })
    Object.defineProperty(video, 'currentTime', { configurable: true, writable: true, value: 0 })
    Object.defineProperty(video, 'videoWidth', { configurable: true, value: 640 })
    Object.defineProperty(video, 'videoHeight', { configurable: true, value: 480 })
    Object.defineProperty(video, 'readyState', { configurable: true, value: 4 })
    video.pause = vi.fn()
    video.play = vi.fn(() => Promise.resolve())

    const drawImageSpy = vi.fn()
    const canvas = document.createElement('canvas')
    vi.spyOn(canvas, 'getContext').mockReturnValue({ drawImage: drawImageSpy } as unknown as CanvasRenderingContext2D)

    const { result, rerender } = renderHook(
      (props) => useAnnotationWorkspaceVideoSync(props),
      { initialProps: baseProps },
    )

    // Set refs before triggering the effect
    act(() => {
      Object.defineProperty(result.current.videoRef, 'current', { value: video, writable: true })
      Object.defineProperty(result.current.displayCanvasRef, 'current', { value: canvas, writable: true })
    })

    // Rerender with videoSrc to activate the paused canvas drawing effect
    rerender({ ...baseProps, videoSrc: '/videos/test.mp4' })

    drawImageSpy.mockClear()

    // Simulate a seek by dispatching seeked event
    act(() => {
      video.dispatchEvent(new Event('seeked'))
    })

    expect(drawImageSpy).toHaveBeenCalledWith(video, 0, 0)
  })
})
