import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { useAnnotationWorkspaceMediaSources } from '@/components/annotation-workspace/useAnnotationWorkspaceMediaSources'
import type { DatasetInfo, EpisodeData } from '@/types'

const dataset: DatasetInfo = {
  id: 'dataset-1',
  name: 'Dataset 1',
  totalEpisodes: 1,
  fps: 30,
  features: {},
  tasks: [],
}

function buildEpisode(cameras: string[]): EpisodeData {
  return {
    meta: { index: 0, length: 10, taskIndex: 0, hasAnnotations: false },
    videoUrls: Object.fromEntries(cameras.map((c) => [c, `/videos/${c}.mp4`])),
    cameras,
    trajectoryData: [],
  }
}

function defaultOptions(episode: EpisodeData) {
  return {
    currentDataset: dataset,
    currentEpisode: episode,
    currentFrame: 0,
    totalFrames: episode.meta.length,
    originalFrameIndex: 0,
    displayAdjustment: null,
    displayActive: false,
    globalTransform: null,
    insertedFrames: new Map(),
    removedFrames: new Set<number>(),
  }
}

describe('useAnnotationWorkspaceMediaSources camera override', () => {
  it('returns cameras[0] synchronously on first render', () => {
    const episode = buildEpisode(['wrist', 'overhead'])

    const { result } = renderHook(() => useAnnotationWorkspaceMediaSources(defaultOptions(episode)))

    expect(result.current.cameraName).toBe('wrist')
    expect(result.current.videoSrc).toBe('/videos/wrist.mp4')
  })

  it('honours a valid override and reflects it in videoSrc', () => {
    const episode = buildEpisode(['wrist', 'overhead'])

    const { result } = renderHook(() => useAnnotationWorkspaceMediaSources(defaultOptions(episode)))

    act(() => {
      result.current.setCameraName('overhead')
    })

    expect(result.current.cameraName).toBe('overhead')
    expect(result.current.videoSrc).toBe('/videos/overhead.mp4')
  })

  it('falls back to cameras[0] when an override no longer matches the episode cameras', () => {
    const initial = buildEpisode(['wrist', 'overhead'])
    const replaced = buildEpisode(['front', 'side'])

    const { result, rerender } = renderHook(
      (props: ReturnType<typeof defaultOptions>) => useAnnotationWorkspaceMediaSources(props),
      { initialProps: defaultOptions(initial) },
    )

    act(() => {
      result.current.setCameraName('overhead')
    })
    expect(result.current.cameraName).toBe('overhead')

    rerender(defaultOptions(replaced))

    expect(result.current.cameraName).toBe('front')
    expect(result.current.videoSrc).toBe('/videos/front.mp4')
  })

  it('returns null cameraName and videoSrc when the episode has no cameras', () => {
    const episode = buildEpisode([])

    const { result } = renderHook(() => useAnnotationWorkspaceMediaSources(defaultOptions(episode)))

    expect(result.current.cameraName).toBeNull()
    expect(result.current.videoSrc).toBeNull()
  })

  it('infers cameras from videoUrls when episode.cameras is empty', () => {
    const episode: EpisodeData = {
      ...buildEpisode([]),
      videoUrls: { wrist: '/videos/wrist.mp4' },
    }

    const { result } = renderHook(() => useAnnotationWorkspaceMediaSources(defaultOptions(episode)))

    expect(result.current.cameras).toEqual(['wrist'])
    expect(result.current.cameraName).toBe('wrist')
  })
})
