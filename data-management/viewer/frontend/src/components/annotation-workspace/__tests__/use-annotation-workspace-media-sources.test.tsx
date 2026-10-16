import { act, render, renderHook, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

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
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('blends inserted frames using adjusted neighbors and cleans image callbacks on unmount', () => {
    const images: Array<{
      width: number
      height: number
      src: string
      onload: (() => void) | null
    }> = []
    class FrameImage {
      width = 16
      height = 16
      src = ''
      onload: (() => void) | null = null
      constructor() {
        images.push(this)
      }
    }
    vi.stubGlobal('Image', FrameImage)
    const context = { globalAlpha: 1, drawImage: vi.fn() }
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
      context as unknown as CanvasRenderingContext2D,
    )
    vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue(
      'data:image/jpeg;base64,preview',
    )
    const options = {
      ...defaultOptions(buildEpisode(['wrist'])),
      originalFrameIndex: null,
      currentFrame: 4,
      removedFrames: new Set([0]),
      insertedFrames: new Map([
        [1, { afterFrameIndex: 1, interpolationFactor: 0.25 }],
        [3, { afterFrameIndex: 3, interpolationFactor: 0.75 }],
        [9, { afterFrameIndex: 9, interpolationFactor: 0.5 }],
      ]),
    }
    function Preview() {
      const media = useAnnotationWorkspaceMediaSources(options)
      return (
        <>
          <canvas ref={media.canvasRef} />
          <output>{media.interpolatedImageUrl ?? 'unavailable'}</output>
        </>
      )
    }
    const { unmount } = render(<Preview />)
    expect(images.map((image) => image.src)).toEqual([
      '/api/datasets/dataset-1/episodes/0/frames/3?camera=wrist',
      '/api/datasets/dataset-1/episodes/0/frames/4?camera=wrist',
    ])
    act(() => images.forEach((image) => image.onload?.()))
    expect(screen.getByRole('status')).toHaveTextContent('data:image/jpeg;base64,preview')
    expect(context.drawImage).toHaveBeenCalledTimes(2)
    unmount()
    expect(images.every((image) => image.onload === null)).toBe(true)
  })

  it('does not generate frame requests without saved episode context and ignores invalid camera choices', () => {
    const options = defaultOptions(buildEpisode(['wrist']))
    const initialProps: Parameters<typeof useAnnotationWorkspaceMediaSources>[0] = {
      ...options,
      currentDataset: null,
    }
    const { result, rerender } = renderHook(
      (props: Parameters<typeof useAnnotationWorkspaceMediaSources>[0]) =>
        useAnnotationWorkspaceMediaSources(props),
      { initialProps },
    )
    expect(result.current.frameImageUrls).toEqual({})
    act(() => result.current.setCameraName(null))
    act(() => result.current.setSelectedCameras(['missing', 'wrist', 'wrist']))
    expect(result.current.selectedCameras).toEqual(['wrist'])
    rerender({ ...options, currentEpisode: null })
    expect(result.current.cameraName).toBeNull()
    expect(result.current.frameImageUrl).toBeNull()
  })

  it('retains a selected camera and preserves multi-view selection on refresh', () => {
    const episode = buildEpisode(['wrist', 'overhead', 'front'])
    const { result, rerender } = renderHook(
      (props: ReturnType<typeof defaultOptions>) => useAnnotationWorkspaceMediaSources(props),
      { initialProps: defaultOptions(episode) },
    )
    expect(result.current.selectedCameras).toEqual(['wrist'])
    act(() => result.current.setSelectedCameras(['wrist', 'overhead']))
    expect(result.current.selectedCameras).toEqual(['wrist', 'overhead'])
    act(() => result.current.setSelectedCameras([]))
    expect(result.current.selectedCameras).toEqual(['wrist', 'overhead'])
    rerender(defaultOptions({ ...episode }))
    expect(result.current.selectedCameras).toEqual(['wrist', 'overhead'])
  })

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

describe('useAnnotationWorkspaceMediaSources video time windows', () => {
  it('returns the [start, end] tuple for the selected camera', () => {
    const episode: EpisodeData = {
      ...buildEpisode(['wrist', 'overhead']),
      videoTimeWindows: { wrist: [1.5, 5.0], overhead: [0, 3.2] },
    }

    const { result } = renderHook(() => useAnnotationWorkspaceMediaSources(defaultOptions(episode)))

    expect(result.current.videoWindow).toEqual([1.5, 5.0])
  })

  it('tracks the camera override when switching cameras', () => {
    const episode: EpisodeData = {
      ...buildEpisode(['wrist', 'overhead']),
      videoTimeWindows: { wrist: [1.5, 5.0], overhead: [0, 3.2] },
    }

    const { result } = renderHook(() => useAnnotationWorkspaceMediaSources(defaultOptions(episode)))

    act(() => {
      result.current.setCameraName('overhead')
    })

    expect(result.current.videoWindow).toEqual([0, 3.2])
  })

  it('returns null when the episode has no videoTimeWindows', () => {
    const episode = buildEpisode(['wrist'])

    const { result } = renderHook(() => useAnnotationWorkspaceMediaSources(defaultOptions(episode)))

    expect(result.current.videoWindow).toBeNull()
  })

  it('returns null when the window for the selected camera is missing', () => {
    const episode: EpisodeData = {
      ...buildEpisode(['wrist', 'overhead']),
      videoTimeWindows: { overhead: [0, 3.2] },
    }

    const { result } = renderHook(() => useAnnotationWorkspaceMediaSources(defaultOptions(episode)))

    expect(result.current.cameraName).toBe('wrist')
    expect(result.current.videoWindow).toBeNull()
  })

  it('returns null when the camera window has the wrong arity', () => {
    const episode: EpisodeData = {
      ...buildEpisode(['wrist']),
      videoTimeWindows: { wrist: [1.5] as unknown as [number, number] },
    }

    const { result } = renderHook(() => useAnnotationWorkspaceMediaSources(defaultOptions(episode)))

    expect(result.current.videoWindow).toBeNull()
  })

  it('always returns the videoUrls map alongside the legacy videoSrc', () => {
    const episode = buildEpisode(['wrist', 'overhead'])

    const { result } = renderHook(() => useAnnotationWorkspaceMediaSources(defaultOptions(episode)))

    expect(result.current.videoUrls).toEqual({
      wrist: '/videos/wrist.mp4',
      overhead: '/videos/overhead.mp4',
    })
    expect(result.current.videoSrc).toBe('/videos/wrist.mp4')
  })
})
