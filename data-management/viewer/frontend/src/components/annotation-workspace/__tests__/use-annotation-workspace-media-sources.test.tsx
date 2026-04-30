import { act, render, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

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

  it('builds frameImageUrl from currentDataset, episode and originalFrameIndex', () => {
    const episode = buildEpisode(['wrist'])
    const opts = { ...defaultOptions(episode), originalFrameIndex: 5 }

    const { result } = renderHook(() => useAnnotationWorkspaceMediaSources(opts))

    expect(result.current.frameImageUrl).toBe(
      '/api/datasets/dataset-1/episodes/0/frames/5?camera=wrist',
    )
  })

  it('returns null frameImageUrl when originalFrameIndex is null (inserted frame)', () => {
    const episode = buildEpisode(['wrist'])
    const opts = { ...defaultOptions(episode), originalFrameIndex: null }

    const { result } = renderHook(() => useAnnotationWorkspaceMediaSources(opts))

    expect(result.current.frameImageUrl).toBeNull()
    expect(result.current.isInsertedFrame).toBe(true)
  })

  it('forwards displayAdjustment and globalTransform into displayFilter', () => {
    const episode = buildEpisode(['wrist'])
    const opts = {
      ...defaultOptions(episode),
      displayAdjustment: { brightness: 1.2, contrast: 1, saturation: 1, hue: 0 },
      displayActive: true,
    }

    const { result } = renderHook(() => useAnnotationWorkspaceMediaSources(opts))

    expect(result.current.displayFilter).toContain('brightness')
  })
})

describe('useAnnotationWorkspaceMediaSources adjacent-frame interpolation', () => {
  function makeEpisodeWithFrames(originalFrameCount: number): EpisodeData {
    return {
      meta: {
        index: 0,
        length: originalFrameCount,
        taskIndex: 0,
        hasAnnotations: false,
      },
      videoUrls: { wrist: '/videos/wrist.mp4' },
      cameras: ['wrist'],
      trajectoryData: Array.from({ length: originalFrameCount }, (_, frame) => ({
        timestamp: frame / 30,
        frame,
        jointPositions: [],
        jointVelocities: [],
        endEffectorPose: [],
        gripperState: 0,
      })),
    }
  }

  it('computes adjacentFrames for an inserted frame at the expected timeline position', () => {
    const episode = makeEpisodeWithFrames(10)
    const insertedFrames = new Map([[3, { afterFrameIndex: 3, interpolationFactor: 0.25 }]])
    const opts = {
      ...defaultOptions(episode),
      originalFrameIndex: null,
      currentFrame: 4,
      insertedFrames,
      removedFrames: new Set<number>(),
    }

    const { result } = renderHook(() => useAnnotationWorkspaceMediaSources(opts))

    expect(result.current.isInsertedFrame).toBe(true)
  })

  it('drives interpolation effect to publish a data URL via canvas blending', async () => {
    const originalImage = window.Image
    const loaders: Array<() => void> = []

    class FakeImage {
      public onload: (() => void) | null = null
      public width = 16
      public height = 9
      private _src = ''
      get src() {
        return this._src
      }
      set src(value: string) {
        this._src = value
        if (this.onload) {
          loaders.push(this.onload)
        }
      }
    }
    // @ts-expect-error happy-dom Image stand-in
    window.Image = FakeImage

    const fakeCtx = { drawImage: vi.fn(), globalAlpha: 1 }
    const originalGetContext = HTMLCanvasElement.prototype.getContext
    HTMLCanvasElement.prototype.getContext = vi.fn(
      () => fakeCtx,
    ) as unknown as typeof originalGetContext
    const originalToDataURL = HTMLCanvasElement.prototype.toDataURL
    HTMLCanvasElement.prototype.toDataURL = vi.fn(
      () => 'data:image/jpeg;base64,fake',
    ) as unknown as typeof originalToDataURL

    try {
      const episode = makeEpisodeWithFrames(10)
      const insertedFrames = new Map([[3, { afterFrameIndex: 3, interpolationFactor: 0.5 }]])
      const opts = {
        ...defaultOptions(episode),
        originalFrameIndex: null,
        currentFrame: 4,
        insertedFrames,
        removedFrames: new Set<number>(),
      }

      let captured: ReturnType<typeof useAnnotationWorkspaceMediaSources> | null = null
      function Harness() {
        const value = useAnnotationWorkspaceMediaSources(opts)
        captured = value
        return <canvas ref={value.canvasRef} />
      }

      render(<Harness />)

      await act(async () => {
        loaders.forEach((cb) => cb())
      })

      expect(captured).not.toBeNull()
      expect(captured!.isInsertedFrame).toBe(true)
      expect(fakeCtx.drawImage).toHaveBeenCalled()
    } finally {
      window.Image = originalImage
      HTMLCanvasElement.prototype.getContext = originalGetContext
      HTMLCanvasElement.prototype.toDataURL = originalToDataURL
    }
  })
})
