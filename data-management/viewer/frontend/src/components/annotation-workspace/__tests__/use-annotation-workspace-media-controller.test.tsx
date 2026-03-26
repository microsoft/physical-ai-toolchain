import { act, renderHook } from '@testing-library/react'
import type { SyntheticEvent } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAnnotationWorkspaceMediaController } from '@/components/annotation-workspace/useAnnotationWorkspaceMediaController'

const dataset = {
  id: 'dataset-1',
  fps: 24,
  name: 'Dataset 1',
  totalEpisodes: 2,
  features: {},
  tasks: [],
}

describe('useAnnotationWorkspaceMediaController', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('derives the primary camera video and frame URLs for the current episode frame', () => {
    const { result } = renderHook(() =>
      useAnnotationWorkspaceMediaController({
        currentDataset: dataset,
        currentEpisode: {
          meta: { index: 3, length: 12, taskIndex: 0, hasAnnotations: false },
          videoUrls: { wrist: '/videos/wrist.mp4', overhead: '/videos/overhead.mp4' },
          cameras: ['wrist', 'overhead'],
          trajectoryData: [],
        },
        currentFrame: 4,
        totalFrames: 12,
        originalFrameIndex: 4,
        activePlaybackRange: null,
        isPlaying: false,
        playbackSpeed: 1,
        autoPlay: false,
        autoLoop: false,
        playbackRangeStart: 0,
        playbackRangeEnd: 11,
        shouldLoopPlaybackRange: false,
        displayAdjustment: null,
        displayActive: false,
        globalTransform: null,
        insertedFrames: new Map(),
        removedFrames: new Set(),
        onSetCurrentFrame: vi.fn(),
        onSetFrameWithinPlaybackRange: vi.fn(),
        onTogglePlayback: vi.fn(),
        onRecordEvent: vi.fn(),
      }),
    )

    expect(result.current.cameraName).toBe('wrist')
    expect(result.current.videoSrc).toBe('/videos/wrist.mp4')
    expect(result.current.frameImageUrl).toBe(
      '/api/datasets/dataset-1/episodes/3/frames/4?camera=wrist',
    )
  })

  it('requests playback after metadata loads when autoplay is armed', () => {
    const togglePlayback = vi.fn()
    const { result } = renderHook(() =>
      useAnnotationWorkspaceMediaController({
        currentDataset: dataset,
        currentEpisode: {
          meta: { index: 3, length: 12, taskIndex: 0, hasAnnotations: false },
          videoUrls: { wrist: '/videos/wrist.mp4' },
          cameras: ['wrist'],
          trajectoryData: [],
        },
        currentFrame: 0,
        totalFrames: 12,
        originalFrameIndex: 0,
        activePlaybackRange: null,
        isPlaying: false,
        playbackSpeed: 1,
        autoPlay: true,
        autoLoop: false,
        playbackRangeStart: 0,
        playbackRangeEnd: 11,
        shouldLoopPlaybackRange: false,
        displayAdjustment: null,
        displayActive: false,
        globalTransform: null,
        insertedFrames: new Map(),
        removedFrames: new Set(),
        onSetCurrentFrame: vi.fn(),
        onSetFrameWithinPlaybackRange: vi.fn(),
        onTogglePlayback: togglePlayback,
        onRecordEvent: vi.fn(),
      }),
    )

    const video = document.createElement('video')
    Object.defineProperty(video, 'duration', { configurable: true, value: 8.4 })

    act(() => {
      result.current.handleLoadedMetadata({
        currentTarget: video,
      } as SyntheticEvent<HTMLVideoElement>)
    })
    expect(togglePlayback).toHaveBeenCalledTimes(1)
  })

  it('triggers autoplay immediately for frame-only episodes without video', () => {
    const togglePlayback = vi.fn()
    renderHook(() =>
      useAnnotationWorkspaceMediaController({
        currentDataset: dataset,
        currentEpisode: {
          meta: { index: 0, length: 100, taskIndex: 0, hasAnnotations: false },
          videoUrls: {},
          cameras: ['il-camera'],
          trajectoryData: [],
        },
        currentFrame: 0,
        totalFrames: 100,
        originalFrameIndex: 0,
        activePlaybackRange: null,
        isPlaying: false,
        playbackSpeed: 1,
        autoPlay: true,
        autoLoop: false,
        playbackRangeStart: 0,
        playbackRangeEnd: 99,
        shouldLoopPlaybackRange: false,
        displayAdjustment: null,
        displayActive: false,
        globalTransform: null,
        insertedFrames: new Map(),
        removedFrames: new Set(),
        onSetCurrentFrame: vi.fn(),
        onSetFrameWithinPlaybackRange: vi.fn(),
        onTogglePlayback: togglePlayback,
        onRecordEvent: vi.fn(),
      }),
    )

    expect(togglePlayback).toHaveBeenCalledTimes(1)
  })
})
