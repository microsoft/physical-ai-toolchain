import { act, render, screen } from '@testing-library/react'
import { forwardRef, useImperativeHandle } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useEpisodeStore, usePlaybackControls, useViewerDisplay } from '@/stores'

import { VideoPlayer } from '../VideoPlayer'

const seekToMock = vi.fn()
const lastPlayerProps: { current: Record<string, unknown> | null } = { current: null }

vi.mock('react-player', () => ({
  __esModule: true,
  default: forwardRef<unknown, Record<string, unknown>>((props, ref) => {
    lastPlayerProps.current = props
    useImperativeHandle(ref, () => ({
      seekTo: seekToMock,
      getInternalPlayer: () => null,
    }))
    return (
      // eslint-disable-next-line jsx-a11y/media-has-caption -- mock element for tests
      <video
        data-testid="react-player"
        data-playing={String(props.playing)}
        data-url={String(props.url ?? '')}
        data-rate={String(props.playbackRate ?? '')}
      />
    )
  }),
}))

vi.mock('@/stores', () => ({
  useEpisodeStore: vi.fn(),
  usePlaybackControls: vi.fn(),
  useViewerDisplay: vi.fn(),
}))

vi.mock('../CameraSelector', () => ({
  CameraSelector: ({
    cameras,
    selectedCamera,
  }: {
    cameras: string[]
    selectedCamera: string
    onSelectCamera: (c: string) => void
  }) => (
    <div
      data-testid="camera-selector"
      data-cameras={cameras.join(',')}
      data-selected={selectedCamera}
    />
  ),
}))

vi.mock('../PlaybackControls', () => ({
  PlaybackControls: ({
    currentFrame,
    totalFrames,
  }: {
    currentFrame: number
    totalFrames: number
    duration: number
    fps: number
  }) => (
    <div data-testid="playback-controls" data-current={currentFrame} data-total={totalFrames} />
  ),
}))

vi.mock('@/components/viewer-display', () => ({
  ViewerDisplayControls: () => <div data-testid="viewer-display-controls" />,
}))

const mockedEpisode = vi.mocked(useEpisodeStore)
const mockedPlayback = vi.mocked(usePlaybackControls)
const mockedDisplay = vi.mocked(useViewerDisplay)

interface SetupOpts {
  episode?: unknown
  currentFrame?: number
  isPlaying?: boolean
  setCurrentFrame?: ReturnType<typeof vi.fn>
  togglePlayback?: ReturnType<typeof vi.fn>
}

function setup(opts: SetupOpts = {}) {
  const setCurrentFrame = opts.setCurrentFrame ?? vi.fn()
  const togglePlayback = opts.togglePlayback ?? vi.fn()
  const episode =
    opts.episode === undefined
      ? {
          videoUrls: { front: 'https://example.com/front.mp4' },
          meta: { length: 300 },
          trajectoryData: [],
        }
      : opts.episode

  mockedEpisode.mockImplementation(((selector: unknown) =>
    typeof selector === 'function'
      ? (selector as (s: unknown) => unknown)({ currentEpisode: episode })
      : episode) as unknown as typeof useEpisodeStore)

  mockedPlayback.mockReturnValue({
    currentFrame: opts.currentFrame ?? 0,
    isPlaying: opts.isPlaying ?? false,
    playbackSpeed: 1,
    setCurrentFrame,
    togglePlayback,
    setPlaybackSpeed: vi.fn(),
  } as unknown as ReturnType<typeof usePlaybackControls>)

  mockedDisplay.mockReturnValue({
    displayAdjustment: { brightness: 1, contrast: 1, saturation: 1, gamma: 1, hue: 0 },
    isActive: false,
    setAdjustment: vi.fn(),
    resetAdjustments: vi.fn(),
  } as unknown as ReturnType<typeof useViewerDisplay>)

  return { setCurrentFrame, togglePlayback }
}

describe('VideoPlayer', () => {
  beforeEach(() => {
    seekToMock.mockReset()
    lastPlayerProps.current = null
  })

  afterEach(() => {
    vi.resetAllMocks()
  })

  it('renders the placeholder when no episode is loaded', () => {
    setup({ episode: null })
    render(<VideoPlayer />)
    expect(screen.getByText('No episode selected')).toBeInTheDocument()
  })

  it('renders the player with the selected camera URL', () => {
    setup()
    render(<VideoPlayer />)
    const player = screen.getByTestId('react-player')
    expect(player).toHaveAttribute('data-url', 'https://example.com/front.mp4')
  })

  it('shows the no-video message when the selected camera has no URL', () => {
    setup({
      episode: {
        videoUrls: { front: '' },
        meta: { length: 300 },
        trajectoryData: [],
      },
    })
    render(<VideoPlayer />)
    expect(screen.getByText('No video available for this camera')).toBeInTheDocument()
  })

  it('mirrors the playing flag onto the player', () => {
    setup({ isPlaying: true })
    render(<VideoPlayer />)
    expect(screen.getByTestId('react-player')).toHaveAttribute('data-playing', 'true')
  })

  it('toggles playback when Space is pressed', () => {
    const { togglePlayback } = setup()
    render(<VideoPlayer />)
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: ' ' }))
    })
    expect(togglePlayback).toHaveBeenCalledTimes(1)
  })

  it('moves one frame backward on ArrowLeft, clamped to zero', () => {
    const { setCurrentFrame } = setup({ currentFrame: 0 })
    render(<VideoPlayer />)
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft' }))
    })
    expect(setCurrentFrame).toHaveBeenCalledWith(0)
  })

  it('moves one frame forward on ArrowRight without clamping', () => {
    const { setCurrentFrame } = setup({ currentFrame: 10 })
    render(<VideoPlayer />)
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }))
    })
    expect(setCurrentFrame).toHaveBeenCalledWith(11)
  })

  it('jumps ten frames forward on ArrowDown without clamping', () => {
    const { setCurrentFrame } = setup({ currentFrame: 5 })
    render(<VideoPlayer />)
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown' }))
    })
    expect(setCurrentFrame).toHaveBeenCalledWith(15)
  })

  it('ignores keyboard shortcuts when an input has focus', () => {
    const { togglePlayback } = setup()
    render(
      <>
        <VideoPlayer />
        <input data-testid="focus-target" />
      </>,
    )
    const input = screen.getByTestId('focus-target')
    input.focus()
    act(() => {
      input.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }))
    })
    expect(togglePlayback).not.toHaveBeenCalled()
  })

  it('appends an HTML media-fragment hint when videoTimeWindows is present', () => {
    setup({
      episode: {
        videoUrls: { front: 'https://example.com/front.mp4' },
        videoTimeWindows: { front: [1.5, 3] },
        meta: { length: 45 },
        trajectoryData: [],
      },
    })
    render(<VideoPlayer />)
    const player = screen.getByTestId('react-player')
    expect(player).toHaveAttribute('data-url', 'https://example.com/front.mp4#t=1.5,3')
  })

  it('does not append a media-fragment hint when no window is provided', () => {
    setup()
    render(<VideoPlayer />)
    const player = screen.getByTestId('react-player')
    expect(player).toHaveAttribute('data-url', 'https://example.com/front.mp4')
  })

  it('stops playback and clamps to the last frame when progress reaches windowEnd', () => {
    const setCurrentFrame = vi.fn()
    const togglePlayback = vi.fn()
    setup({
      episode: {
        videoUrls: { front: 'https://example.com/front.mp4' },
        videoTimeWindows: { front: [1.5, 3] },
        meta: { length: 45 },
        trajectoryData: [],
      },
      isPlaying: true,
      setCurrentFrame,
      togglePlayback,
    })
    render(<VideoPlayer />)

    const onProgress = lastPlayerProps.current?.onProgress as
      | ((state: { playedSeconds: number }) => void)
      | undefined
    expect(onProgress).toBeTypeOf('function')

    act(() => {
      onProgress!({ playedSeconds: 3.0 })
    })

    expect(seekToMock).toHaveBeenCalledWith(3, 'seconds')
    expect(setCurrentFrame).toHaveBeenCalledWith(44)
    expect(togglePlayback).toHaveBeenCalledTimes(1)
  })

  it('advances the frame from the window-relative offset while playing inside the window', () => {
    const setCurrentFrame = vi.fn()
    setup({
      episode: {
        videoUrls: { front: 'https://example.com/front.mp4' },
        videoTimeWindows: { front: [1.5, 3] },
        meta: { length: 45 },
        trajectoryData: [],
      },
      isPlaying: true,
      setCurrentFrame,
    })
    render(<VideoPlayer />)

    const onProgress = lastPlayerProps.current?.onProgress as
      | ((state: { playedSeconds: number }) => void)
      | undefined
    expect(onProgress).toBeTypeOf('function')

    // fps = meta.length / windowDuration = 45 / 1.5 = 30
    // playedSeconds 2.0 → episodeTime 0.5 → frame Math.floor(0.5 * 30) = 15.
    act(() => {
      onProgress!({ playedSeconds: 2.0 })
    })

    expect(setCurrentFrame).toHaveBeenLastCalledWith(15)
  })
})
