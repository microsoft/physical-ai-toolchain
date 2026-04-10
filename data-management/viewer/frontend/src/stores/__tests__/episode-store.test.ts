import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import type { EpisodeData, EpisodeMeta } from '@/types'

import {
  useCurrentEpisodeIndex,
  useEpisodeNavigation,
  useEpisodeStore,
  usePlaybackControls,
} from '../episode-store'

const mockEpisodes: EpisodeMeta[] = [
  { index: 0, length: 120, taskIndex: 0, task: 'pick', hasAnnotations: false },
  { index: 1, length: 80, taskIndex: 0, task: 'pick', hasAnnotations: true },
  { index: 2, length: 200, taskIndex: 1, task: 'place', hasAnnotations: false },
]

const mockEpisodeData: EpisodeData = {
  meta: mockEpisodes[1],
  videoUrls: { front: '/video/front.mp4', wrist: '/video/wrist.mp4' },
  cameras: ['front', 'wrist'],
  trajectoryData: [
    {
      timestamp: 0,
      frame: 0,
      jointPositions: [0, 0, 0],
      jointVelocities: [0, 0, 0],
      endEffectorPose: [0, 0, 0, 0, 0, 0],
      gripperState: 0,
    },
  ],
}

describe('useEpisodeStore', () => {
  beforeEach(() => {
    useEpisodeStore.getState().reset()
  })

  it('starts with initial state', () => {
    const state = useEpisodeStore.getState()
    expect(state.episodes).toEqual([])
    expect(state.currentEpisode).toBeNull()
    expect(state.currentIndex).toBe(-1)
    expect(state.isLoading).toBe(false)
    expect(state.currentFrame).toBe(0)
    expect(state.isPlaying).toBe(false)
    expect(state.playbackSpeed).toBe(1.0)
  })

  describe('setEpisodes', () => {
    it('sets episode list and clears error', () => {
      useEpisodeStore.getState().setError('old error')
      useEpisodeStore.getState().setEpisodes(mockEpisodes)

      const state = useEpisodeStore.getState()
      expect(state.episodes).toEqual(mockEpisodes)
      expect(state.error).toBeNull()
    })
  })

  describe('setCurrentEpisode', () => {
    it('sets current episode and resets frame/playback', () => {
      useEpisodeStore.getState().setCurrentEpisode(mockEpisodeData)

      const state = useEpisodeStore.getState()
      expect(state.currentEpisode).toEqual(mockEpisodeData)
      expect(state.currentIndex).toBe(1)
      expect(state.currentFrame).toBe(0)
      expect(state.isPlaying).toBe(false)
    })

    it('sets index to -1 when cleared', () => {
      useEpisodeStore.getState().setCurrentEpisode(mockEpisodeData)
      useEpisodeStore.getState().setCurrentEpisode(null)

      expect(useEpisodeStore.getState().currentIndex).toBe(-1)
    })
  })

  describe('navigation', () => {
    beforeEach(() => {
      useEpisodeStore.getState().setEpisodes(mockEpisodes)
      useEpisodeStore.getState().navigateToEpisode(0)
    })

    it('navigates to a valid episode index', () => {
      useEpisodeStore.getState().navigateToEpisode(2)

      const state = useEpisodeStore.getState()
      expect(state.currentIndex).toBe(2)
      expect(state.isLoading).toBe(true)
    })

    it('ignores out-of-bounds navigation', () => {
      useEpisodeStore.getState().navigateToEpisode(10)
      expect(useEpisodeStore.getState().currentIndex).toBe(0)

      useEpisodeStore.getState().navigateToEpisode(-1)
      expect(useEpisodeStore.getState().currentIndex).toBe(0)
    })

    it('navigates to next episode', () => {
      useEpisodeStore.getState().nextEpisode()
      expect(useEpisodeStore.getState().currentIndex).toBe(1)
    })

    it('does not go past last episode', () => {
      useEpisodeStore.getState().navigateToEpisode(2)
      useEpisodeStore.getState().nextEpisode()
      expect(useEpisodeStore.getState().currentIndex).toBe(2)
    })

    it('navigates to previous episode', () => {
      useEpisodeStore.getState().navigateToEpisode(2)
      useEpisodeStore.getState().previousEpisode()
      expect(useEpisodeStore.getState().currentIndex).toBe(1)
    })

    it('does not go before first episode', () => {
      useEpisodeStore.getState().previousEpisode()
      expect(useEpisodeStore.getState().currentIndex).toBe(0)
    })
  })

  describe('playback controls', () => {
    beforeEach(() => {
      useEpisodeStore.getState().setCurrentEpisode(mockEpisodeData)
    })

    it('sets current frame clamped within bounds', () => {
      useEpisodeStore.getState().setCurrentFrame(50)
      expect(useEpisodeStore.getState().currentFrame).toBe(50)
    })

    it('clamps frame to 0 for negative values', () => {
      useEpisodeStore.getState().setCurrentFrame(-5)
      expect(useEpisodeStore.getState().currentFrame).toBe(0)
    })

    it('clamps frame to max for oversized values', () => {
      useEpisodeStore.getState().setCurrentFrame(9999)
      expect(useEpisodeStore.getState().currentFrame).toBe(mockEpisodeData.meta.length - 1)
    })

    it('toggles playback', () => {
      useEpisodeStore.getState().togglePlayback()
      expect(useEpisodeStore.getState().isPlaying).toBe(true)

      useEpisodeStore.getState().togglePlayback()
      expect(useEpisodeStore.getState().isPlaying).toBe(false)
    })

    it('sets playback speed', () => {
      useEpisodeStore.getState().setPlaybackSpeed(2.0)
      expect(useEpisodeStore.getState().playbackSpeed).toBe(2.0)
    })
  })

  describe('reset', () => {
    it('restores initial state', () => {
      useEpisodeStore.getState().setEpisodes(mockEpisodes)
      useEpisodeStore.getState().setCurrentEpisode(mockEpisodeData)
      useEpisodeStore.getState().togglePlayback()
      useEpisodeStore.getState().reset()

      const state = useEpisodeStore.getState()
      expect(state.episodes).toEqual([])
      expect(state.currentEpisode).toBeNull()
      expect(state.currentIndex).toBe(-1)
      expect(state.isPlaying).toBe(false)
    })
  })

  describe('setLoading', () => {
    it('sets loading to true', () => {
      useEpisodeStore.getState().setLoading(true)
      expect(useEpisodeStore.getState().isLoading).toBe(true)
    })

    it('sets loading to false', () => {
      useEpisodeStore.getState().setLoading(true)
      useEpisodeStore.getState().setLoading(false)
      expect(useEpisodeStore.getState().isLoading).toBe(false)
    })
  })
})

describe('episode-store selector hooks', () => {
  beforeEach(() => {
    useEpisodeStore.getState().reset()
  })

  describe('useCurrentEpisodeIndex', () => {
    it('returns -1 when no episode is selected', () => {
      const { result } = renderHook(() => useCurrentEpisodeIndex())
      expect(result.current).toBe(-1)
    })

    it('returns current index after setting episode', () => {
      useEpisodeStore.getState().setEpisodes(mockEpisodes)
      useEpisodeStore.getState().setCurrentEpisode(mockEpisodeData)

      const { result } = renderHook(() => useCurrentEpisodeIndex())
      expect(result.current).toBe(1)
    })
  })

  describe('useEpisodeNavigation', () => {
    it('disables navigation with no episodes', () => {
      const { result } = renderHook(() => useEpisodeNavigation())
      expect(result.current.canGoNext).toBe(false)
      expect(result.current.canGoPrevious).toBe(false)
    })

    it('enables next when not at last episode', () => {
      useEpisodeStore.getState().setEpisodes(mockEpisodes)
      useEpisodeStore.getState().navigateToEpisode(0)

      const { result } = renderHook(() => useEpisodeNavigation())
      expect(result.current.canGoNext).toBe(true)
      expect(result.current.canGoPrevious).toBe(false)
    })

    it('enables previous when not at first episode', () => {
      useEpisodeStore.getState().setEpisodes(mockEpisodes)
      useEpisodeStore.getState().navigateToEpisode(2)

      const { result } = renderHook(() => useEpisodeNavigation())
      expect(result.current.canGoNext).toBe(false)
      expect(result.current.canGoPrevious).toBe(true)
    })

    it('provides navigation functions', () => {
      const { result } = renderHook(() => useEpisodeNavigation())
      expect(result.current.nextEpisode).toBeTypeOf('function')
      expect(result.current.previousEpisode).toBeTypeOf('function')
    })
  })

  describe('usePlaybackControls', () => {
    it('returns default playback state', () => {
      const { result } = renderHook(() => usePlaybackControls())
      expect(result.current.currentFrame).toBe(0)
      expect(result.current.isPlaying).toBe(false)
      expect(result.current.playbackSpeed).toBe(1.0)
    })

    it('provides playback control functions', () => {
      const { result } = renderHook(() => usePlaybackControls())
      expect(result.current.setCurrentFrame).toBeTypeOf('function')
      expect(result.current.togglePlayback).toBeTypeOf('function')
      expect(result.current.setPlaybackSpeed).toBeTypeOf('function')
    })
  })
})
