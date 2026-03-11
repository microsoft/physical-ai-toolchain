import { renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useFramePrefetch } from '@/components/annotation-workspace/useFramePrefetch'

describe('useFramePrefetch', () => {
  const createdSrcs: string[] = []

  afterEach(() => {
    createdSrcs.length = 0
    vi.restoreAllMocks()
  })

  function mockImageConstructor() {
    vi.spyOn(globalThis, 'Image').mockImplementation(
      function (this: HTMLImageElement) {
        Object.defineProperty(this, 'src', {
          set(value: string) { createdSrcs.push(value) },
          get() { return '' },
          configurable: true,
        })
      } as unknown as () => HTMLImageElement,
    )
  }

  it('does not prefetch when videoSrc is present', () => {
    mockImageConstructor()

    renderHook(() => useFramePrefetch({
      datasetId: 'ds-1',
      episodeIndex: 0,
      cameraName: 'il-camera',
      currentFrame: 5,
      totalFrames: 100,
      isPlaying: true,
      videoSrc: '/videos/wrist.mp4',
      lookahead: 5,
    }))

    expect(createdSrcs).toHaveLength(0)
  })

  it('does not prefetch when paused', () => {
    mockImageConstructor()

    renderHook(() => useFramePrefetch({
      datasetId: 'ds-1',
      episodeIndex: 0,
      cameraName: 'il-camera',
      currentFrame: 5,
      totalFrames: 100,
      isPlaying: false,
      videoSrc: null,
      lookahead: 5,
    }))

    expect(createdSrcs).toHaveLength(0)
  })

  it('does not prefetch when cameraName is null', () => {
    mockImageConstructor()

    renderHook(() => useFramePrefetch({
      datasetId: 'ds-1',
      episodeIndex: 0,
      cameraName: null,
      currentFrame: 5,
      totalFrames: 100,
      isPlaying: true,
      videoSrc: null,
      lookahead: 5,
    }))

    expect(createdSrcs).toHaveLength(0)
  })

  it('prefetches upcoming frames during frame-only playback', () => {
    mockImageConstructor()

    renderHook(() => useFramePrefetch({
      datasetId: 'ds-1',
      episodeIndex: 2,
      cameraName: 'il-camera',
      currentFrame: 10,
      totalFrames: 100,
      isPlaying: true,
      videoSrc: null,
      lookahead: 3,
    }))

    expect(createdSrcs).toHaveLength(3)
    expect(createdSrcs[0]).toBe('/api/datasets/ds-1/episodes/2/frames/11?camera=il-camera')
    expect(createdSrcs[1]).toBe('/api/datasets/ds-1/episodes/2/frames/12?camera=il-camera')
    expect(createdSrcs[2]).toBe('/api/datasets/ds-1/episodes/2/frames/13?camera=il-camera')
  })

  it('clamps prefetch to the end of the episode', () => {
    mockImageConstructor()

    renderHook(() => useFramePrefetch({
      datasetId: 'ds-1',
      episodeIndex: 0,
      cameraName: 'cam',
      currentFrame: 98,
      totalFrames: 100,
      isPlaying: true,
      videoSrc: null,
      lookahead: 5,
    }))

    expect(createdSrcs).toHaveLength(1)
    expect(createdSrcs[0]).toContain('/frames/99?camera=cam')
  })

  it('skips already-prefetched frames on re-render', () => {
    mockImageConstructor()

    const { rerender } = renderHook(
      (props) => useFramePrefetch(props),
      {
        initialProps: {
          datasetId: 'ds-1',
          episodeIndex: 0,
          cameraName: 'cam',
          currentFrame: 5,
          totalFrames: 100,
          isPlaying: true,
          videoSrc: null as string | null,
          lookahead: 3,
        },
      },
    )

    expect(createdSrcs).toHaveLength(3)
    expect(createdSrcs).toContain('/api/datasets/ds-1/episodes/0/frames/6?camera=cam')
    expect(createdSrcs).toContain('/api/datasets/ds-1/episodes/0/frames/7?camera=cam')
    expect(createdSrcs).toContain('/api/datasets/ds-1/episodes/0/frames/8?camera=cam')

    createdSrcs.length = 0

    rerender({
      datasetId: 'ds-1',
      episodeIndex: 0,
      cameraName: 'cam',
      currentFrame: 7,
      totalFrames: 100,
      isPlaying: true,
      videoSrc: null,
      lookahead: 3,
    })

    expect(createdSrcs).toHaveLength(2)
    expect(createdSrcs).toContain('/api/datasets/ds-1/episodes/0/frames/9?camera=cam')
    expect(createdSrcs).toContain('/api/datasets/ds-1/episodes/0/frames/10?camera=cam')
  })
})
