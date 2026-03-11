import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const hoisted = vi.hoisted(() => ({
  detectionState: {
    data: {
      processed_frames: 12,
      detections_by_frame: [
        { frame: 1, detections: [{ class_name: 'cube', confidence: 0.82, bbox: [0, 0, 10, 10] }] },
        { frame: 2, detections: [{ class_name: 'arm', confidence: 0.76, bbox: [2, 2, 12, 12] }] },
      ],
      total_detections: 2,
      total_frames: 12,
      class_summary: { cube: 1, arm: 1 },
    },
    filteredData: {
      processed_frames: 12,
      detections_by_frame: [
        { frame: 1, detections: [{ class_name: 'cube', confidence: 0.82, bbox: [0, 0, 10, 10] }] },
        { frame: 2, detections: [{ class_name: 'arm', confidence: 0.76, bbox: [2, 2, 12, 12] }] },
      ],
      total_detections: 2,
      total_frames: 12,
      class_summary: { cube: 1, arm: 1 },
    },
    isLoading: false,
    isRunning: false,
    error: null,
    needsRerun: false,
    filters: { classes: [], minConfidence: 0.25 },
    setFilters: vi.fn(),
    runDetection: vi.fn(),
    availableClasses: ['cube', 'arm'],
  },
  playbackState: {
    currentFrame: 1,
    isPlaying: false,
    playbackSpeed: 1,
    setCurrentFrame: vi.fn(),
    togglePlayback: vi.fn(),
    setPlaybackSpeed: vi.fn(),
  },
  datasetState: { currentDataset: { id: 'dataset-1' } },
  episodeState: { currentEpisode: { meta: { index: 3, length: 12, taskIndex: 0, hasAnnotations: false } } },
}))

vi.mock('@/hooks/use-object-detection', () => ({
  useObjectDetection: () => hoisted.detectionState,
}))

vi.mock('@/stores', () => ({
  useDatasetStore: (selector: (state: unknown) => unknown) => selector(hoisted.datasetState),
  useEpisodeStore: (selector: (state: unknown) => unknown) => selector(hoisted.episodeState),
  usePlaybackControls: () => hoisted.playbackState,
}))

import { useDetectionPanelState } from '@/components/object-detection/useDetectionPanelState'

describe('useDetectionPanelState', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    hoisted.detectionState.isRunning = false
    hoisted.playbackState.currentFrame = 1
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('derives the current frame detections and overlay image URL', () => {
    const { result } = renderHook(() => useDetectionPanelState())

    expect(result.current.currentDetections).toEqual([
      { class_name: 'cube', confidence: 0.82, bbox: [0, 0, 10, 10] },
    ])
    expect(result.current.imageUrl).toBe('/api/datasets/dataset-1/episodes/3/frames/1?camera=il-camera')
    expect(result.current.totalFrames).toBe(12)
  })

  it('advances progress while detection is running', () => {
    hoisted.detectionState.isRunning = true

    const { result } = renderHook(() => useDetectionPanelState())

    act(() => {
      vi.advanceTimersByTime(300)
    })

    expect(result.current.progress).toBeGreaterThan(0)
    expect(result.current.progress).toBeLessThanOrEqual(95)
  })
})
