import { act, fireEvent, render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AnnotationWorkspacePlaybackCard } from '@/components/annotation-workspace/AnnotationWorkspacePlaybackCard'

function renderPlaybackCard(overrides: Record<string, unknown> = {}) {
  const defaultProps = {
    compact: false,
    canvasRef: createRef<HTMLCanvasElement>(),
    videoRef: createRef<HTMLVideoElement>(),
    videoSrc: null,
    onVideoEnded: vi.fn(),
    onLoadedMetadata: vi.fn(),
    isInsertedFrame: false,
    interpolatedImageUrl: null,
    currentFrame: 0,
    totalFrames: 100,
    resizeOutput: null,
    frameImageUrl: '/api/datasets/test/episodes/0/frames/0?camera=wrist',
    cameras: ['wrist'],
    selectedCamera: 'wrist',
    onSelectCamera: vi.fn(),
    isPlaying: false,
    onTogglePlayback: vi.fn(),
    onStepFrame: vi.fn(),
    playbackSpeed: 1,
    onSetPlaybackSpeed: vi.fn(),
    autoPlay: false,
    onSetAutoPlay: vi.fn(),
    autoLoop: false,
    onSetAutoLoop: vi.fn(),
    playbackRangeStart: 0,
    playbackRangeEnd: 99,
    onSetFrameWithinPlaybackRange: vi.fn(),
    playbackRangeHighlight: null,
    playbackRangeLabel: null,
  }

  return render(<AnnotationWorkspacePlaybackCard {...defaultProps} {...overrides} />)
}

describe('AnnotationWorkspacePlaybackCard', () => {
  it.each([false, true])('handles a follower play failure after unmount=%s', async (unmounted) => {
    const onTogglePlayback = vi.fn()
    const { container, unmount } = renderPlaybackCard({
      cameras: ['wrist', 'overhead'],
      selectedCameras: ['wrist', 'overhead'],
      videoSrc: '/wrist.mp4',
      videoUrls: { wrist: '/wrist.mp4', overhead: '/overhead.mp4' },
      isPlaying: true,
      onTogglePlayback,
    })
    const follower = container.querySelector<HTMLVideoElement>('video[data-camera="overhead"]')!
    let rejectPlay!: (cause: Error) => void
    vi.spyOn(follower, 'play').mockReturnValueOnce(
      new Promise((_, reject) => {
        rejectPlay = reject
      }),
    )
    fireEvent.loadedMetadata(follower)
    if (unmounted) unmount()

    await act(async () => rejectPlay(new Error('Synthetic decoder failure')))

    if (unmounted) expect(onTogglePlayback).not.toHaveBeenCalled()
    else {
      expect(screen.getByText('overhead: video unavailable')).toBeVisible()
      expect(onTogglePlayback).toHaveBeenCalledOnce()
      fireEvent.error(follower)
      expect(onTogglePlayback).toHaveBeenCalledOnce()
    }
  })

  it.each([Number.NaN, 0, 0.1])(
    'synchronizes followers without timing windows (duration=%s)',
    (duration) => {
      const { container } = renderPlaybackCard({
        cameras: ['wrist', 'overhead'],
        selectedCameras: ['wrist', 'overhead'],
        videoSrc: '/wrist.mp4',
        videoUrls: { wrist: '/wrist.mp4', overhead: '/overhead.mp4' },
        currentFrame: 30,
        originalFrameIndex: null,
        sourceFrameCount: 0,
        datasetFps: 30,
      })
      const follower = container.querySelector<HTMLVideoElement>('video[data-camera="overhead"]')!
      vi.spyOn(follower, 'duration', 'get').mockReturnValue(duration)
      const pause = vi.spyOn(follower, 'pause')

      fireEvent.loadedMetadata(follower)

      expect(follower.currentTime).toBeCloseTo(duration > 0 ? duration - 0.001 : 1)
      expect(follower.playbackRate).toBe(1)
      expect(pause).toHaveBeenCalled()
    },
  )

  it('does not restart an already-playing follower or seek within the frame tolerance', () => {
    const { container } = renderPlaybackCard({
      cameras: ['wrist', 'overhead'],
      selectedCameras: ['wrist', 'overhead'],
      videoSrc: '/wrist.mp4',
      videoUrls: { wrist: '/wrist.mp4', overhead: '/overhead.mp4' },
      currentFrame: 0,
      originalFrameIndex: 0,
      isPlaying: true,
      datasetFps: 30,
    })
    const follower = container.querySelector<HTMLVideoElement>('video[data-camera="overhead"]')!
    vi.spyOn(follower, 'paused', 'get').mockReturnValue(false)
    const play = vi.spyOn(follower, 'play')
    follower.currentTime = 0.01

    fireEvent.loadedMetadata(follower)

    expect(follower.currentTime).toBe(0.01)
    expect(play).not.toHaveBeenCalled()
  })

  it('keeps hidden-document followers paused even when playback is active', () => {
    const { container } = renderPlaybackCard({
      cameras: ['wrist', 'overhead'],
      selectedCameras: ['wrist', 'overhead'],
      videoSrc: '/wrist.mp4',
      videoUrls: { wrist: '/wrist.mp4', overhead: '/overhead.mp4' },
      currentFrame: 0,
      originalFrameIndex: 0,
      isPlaying: true,
    })
    const follower = container.querySelector<HTMLVideoElement>('video[data-camera="overhead"]')!
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    const play = vi.spyOn(follower, 'play')
    const pause = vi.spyOn(follower, 'pause')

    fireEvent.loadedMetadata(follower)

    expect(play).not.toHaveBeenCalled()
    expect(pause).toHaveBeenCalled()
  })

  it('clears failed primary media after metadata recovery and releases its ref on unmount', () => {
    const videoRef = createRef<HTMLVideoElement>()
    const { container, unmount } = renderPlaybackCard({ videoRef, videoSrc: '/wrist.mp4' })
    const primary = container.querySelector('video')!
    fireEvent.error(primary)
    fireEvent.error(primary)
    expect(screen.getByText('wrist: video unavailable')).toBeVisible()

    fireEvent.loadedMetadata(primary)

    expect(screen.queryByText('wrist: video unavailable')).not.toBeInTheDocument()
    expect(videoRef.current).toBe(primary)
    unmount()
    expect(videoRef.current).toBeNull()
  })

  it('renders only the primary video when no explicit camera selection exists', () => {
    const { container } = renderPlaybackCard({
      videoSrc: '/wrist.mp4',
      videoUrls: { wrist: '/wrist.mp4', overhead: '/overhead.mp4', front: '' },
      selectedCameras: undefined,
    })

    expect(
      Array.from(container.querySelectorAll('video')).map((video) => video.dataset.camera),
    ).toEqual(['wrist'])
  })

  it('recognizes already-ready media without showing a delayed loading overlay', () => {
    const readyState = vi.spyOn(HTMLMediaElement.prototype, 'readyState', 'get').mockReturnValue(1)
    renderPlaybackCard({ videoSrc: '/ready.mp4', frameImageUrl: null })
    act(() => vi.advanceTimersByTime(250))
    expect(screen.queryByText('Loading video…')).not.toBeInTheDocument()
    expect(document.querySelector('video')?.readyState).toBe(1)
    readyState.mockRestore()
  })

  it('pauses the video group and requests one playback stop when the document becomes hidden', () => {
    const onTogglePlayback = vi.fn()
    renderPlaybackCard({ videoSrc: '/playing.mp4', isPlaying: true, onTogglePlayback })
    const video = document.querySelector('video')!
    video.pause = vi.fn()
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    fireEvent(document, new Event('visibilitychange'))
    expect(video.pause).toHaveBeenCalledOnce()
    expect(onTogglePlayback).toHaveBeenCalledOnce()
    hidden.mockRestore()
  })

  it.each([true, false])(
    'names and dispatches frame, reset and mode controls (compact=%s)',
    (compact) => {
      const onStepFrame = vi.fn()
      const onSetFrameWithinPlaybackRange = vi.fn()
      const onSetAutoPlay = vi.fn()
      const onSetAutoLoop = vi.fn()
      renderPlaybackCard({
        compact,
        currentFrame: 15,
        playbackRangeStart: 10,
        playbackRangeEnd: 20,
        onStepFrame,
        onSetFrameWithinPlaybackRange,
        onSetAutoPlay,
        onSetAutoLoop,
      })
      fireEvent.click(screen.getByRole('button', { name: 'Previous frame' }))
      fireEvent.click(screen.getByRole('button', { name: 'Next frame' }))
      expect(onStepFrame.mock.calls).toEqual([[-1], [1]])
      fireEvent.click(screen.getByRole('button', { name: 'Reset playback' }))
      expect(onSetFrameWithinPlaybackRange).toHaveBeenCalledWith(10)
      fireEvent.click(screen.getByRole('button', { name: compact ? 'Auto-play' : 'Auto' }))
      fireEvent.click(screen.getByRole('button', { name: compact ? 'Loop playback' : 'Loop' }))
      expect(onSetAutoPlay).toHaveBeenCalledWith(true)
      expect(onSetAutoLoop).toHaveBeenCalledWith(true)
      fireEvent.change(screen.getByRole('slider', { name: 'Playback frame' }), {
        target: { value: '16' },
      })
      expect(onSetFrameWithinPlaybackRange).toHaveBeenLastCalledWith(16)
    },
  )

  it('marks failed primary and follower frames unavailable and clears those states after image recovery', () => {
    const onTogglePlayback = vi.fn()
    renderPlaybackCard({
      compact: true,
      cameras: ['wrist', 'overhead'],
      selectedCameras: ['wrist', 'overhead'],
      frameImageUrls: { overhead: '/frames/overhead' },
      isPlaying: true,
      onTogglePlayback,
    })
    const primary = screen.getByAltText('Frame 0')
    const follower = screen.getByAltText('overhead frame 0')
    fireEvent.error(primary)
    fireEvent.error(follower)
    expect(screen.getByText('overhead: preview unavailable')).toBeVisible()
    expect(onTogglePlayback).toHaveBeenCalledOnce()
    fireEvent.load(primary)
    fireEvent.load(follower)
    expect(screen.queryByText('overhead: preview unavailable')).not.toBeInTheDocument()
  })

  it('renders selected frame-only views and identifies unavailable views', () => {
    renderPlaybackCard({
      cameras: ['wrist', 'overhead', 'front'],
      selectedCameras: ['wrist', 'overhead', 'front'],
      frameImageUrls: { wrist: '/frames/0?camera=wrist', overhead: '/frames/0?camera=overhead' },
    })
    expect(screen.getByAltText('Frame 0')).toBeInTheDocument()
    expect(screen.getByAltText('overhead frame 0')).toHaveAttribute(
      'src',
      '/frames/0?camera=overhead',
    )
    expect(screen.getByText('front: preview unavailable')).toBeVisible()
  })

  it('identifies unsupported edited previews without claiming transformed output', () => {
    renderPlaybackCard({ videoSrc: '/video.mp4', resizeOutput: { width: 640, height: 480 } })
    expect(screen.getByText(/edited preview unavailable/i)).toBeInTheDocument()
    expect(screen.queryByText(/Output:/)).not.toBeInTheDocument()
  })

  it('does not report a cancelled follower play request as a media failure', async () => {
    const onTogglePlayback = vi.fn()
    const { container } = renderPlaybackCard({
      cameras: ['wrist', 'overhead'],
      selectedCameras: ['wrist', 'overhead'],
      videoSrc: '/videos/wrist.mp4',
      videoUrls: { wrist: '/videos/wrist.mp4', overhead: '/videos/overhead.mp4' },
      videoTimeWindows: { wrist: [2, 3], overhead: [5, 6] },
      isPlaying: true,
      onTogglePlayback,
    })
    const follower = container.querySelector<HTMLVideoElement>('video[data-camera="overhead"]')!
    vi.spyOn(follower, 'play').mockRejectedValueOnce(new DOMException('Cancelled', 'AbortError'))
    await act(async () => {
      fireEvent.loadedMetadata(follower)
    })
    expect(screen.queryByText(/overhead.*unavailable/i)).not.toBeInTheDocument()
    expect(onTogglePlayback).not.toHaveBeenCalled()
  })

  it.each([1, 2])('synchronizes a follower with a %is window and stops on error', (duration) => {
    const onTogglePlayback = vi.fn()
    const { container } = renderPlaybackCard({
      cameras: ['wrist', 'overhead'],
      selectedCameras: ['wrist', 'overhead'],
      videoSrc: '/videos/wrist.mp4',
      videoUrls: { wrist: '/videos/wrist.mp4', overhead: '/videos/overhead.mp4' },
      videoTimeWindows: { wrist: [2, 3], overhead: [5, 5 + duration] },
      currentFrame: 3,
      originalFrameIndex: 3,
      totalFrames: 30,
      sourceFrameCount: 30,
      datasetFps: 30,
      isPlaying: true,
      playbackSpeed: 1.5,
      onTogglePlayback,
    })
    const follower = container.querySelector<HTMLVideoElement>('video[data-camera="overhead"]')!
    fireEvent.loadedMetadata(follower)
    expect(follower.currentTime).toBeCloseTo(5 + duration / 10)
    expect(follower.playbackRate).toBe(1.5 * duration)
    fireEvent.error(follower)
    expect(onTogglePlayback).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/overhead.*unavailable/i)).toBeInTheDocument()
  })

  it('mounts only selected camera videos', () => {
    const { container } = renderPlaybackCard({
      cameras: ['wrist', 'overhead', 'front'],
      selectedCameras: ['wrist', 'overhead'],
      videoSrc: '/videos/wrist.mp4',
      videoUrls: {
        wrist: '/videos/wrist.mp4',
        overhead: '/videos/overhead.mp4',
        front: '/videos/front.mp4',
      },
    })
    expect(
      Array.from(container.querySelectorAll('video')).map((video) => video.dataset.camera),
    ).toEqual(['wrist', 'overhead'])
  })

  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('shows loading overlay for HDF5 episodes before first image loads', () => {
    renderPlaybackCard({
      videoSrc: null,
      frameImageUrl: '/api/datasets/test/episodes/0/frames/0?camera=wrist',
    })

    expect(screen.getByRole('status')).toHaveTextContent('Loading episode…')
  })

  it('hides loading overlay after frame image loads', () => {
    renderPlaybackCard({
      videoSrc: null,
      frameImageUrl: '/api/datasets/test/episodes/0/frames/0?camera=wrist',
    })

    const img = screen.getByAltText('Frame 0')
    fireEvent.load(img)

    expect(screen.queryByText('Loading episode…')).not.toBeInTheDocument()
  })

  it('does not show HDF5 loading overlay for video episodes', () => {
    renderPlaybackCard({
      videoSrc: '/videos/wrist.mp4',
      frameImageUrl: null,
    })

    expect(screen.queryByText('Loading episode…')).not.toBeInTheDocument()
  })

  it('resets loading state when episode changes', () => {
    const { rerender } = render(
      <AnnotationWorkspacePlaybackCard
        compact={false}
        canvasRef={createRef<HTMLCanvasElement>()}
        videoRef={createRef<HTMLVideoElement>()}
        videoSrc={null}
        onVideoEnded={vi.fn()}
        onLoadedMetadata={vi.fn()}
        isInsertedFrame={false}
        interpolatedImageUrl={null}
        currentFrame={0}
        totalFrames={100}
        resizeOutput={null}
        frameImageUrl="/api/datasets/test/episodes/0/frames/0?camera=wrist"
        cameras={['wrist']}
        selectedCamera="wrist"
        onSelectCamera={vi.fn()}
        isPlaying={false}
        onTogglePlayback={vi.fn()}
        onStepFrame={vi.fn()}
        playbackSpeed={1}
        onSetPlaybackSpeed={vi.fn()}
        autoPlay={false}
        onSetAutoPlay={vi.fn()}
        autoLoop={false}
        onSetAutoLoop={vi.fn()}
        playbackRangeStart={0}
        playbackRangeEnd={99}
        onSetFrameWithinPlaybackRange={vi.fn()}
        playbackRangeHighlight={null}
        playbackRangeLabel={null}
      />,
    )

    // First image loads
    const img = screen.getByAltText('Frame 0')
    fireEvent.load(img)
    expect(screen.queryByText('Loading episode…')).not.toBeInTheDocument()

    // Switch episode — loading overlay should reappear
    rerender(
      <AnnotationWorkspacePlaybackCard
        compact={false}
        canvasRef={createRef<HTMLCanvasElement>()}
        videoRef={createRef<HTMLVideoElement>()}
        videoSrc={null}
        onVideoEnded={vi.fn()}
        onLoadedMetadata={vi.fn()}
        isInsertedFrame={false}
        interpolatedImageUrl={null}
        currentFrame={0}
        totalFrames={80}
        resizeOutput={null}
        frameImageUrl="/api/datasets/test/episodes/1/frames/0?camera=wrist"
        cameras={['wrist']}
        selectedCamera="wrist"
        onSelectCamera={vi.fn()}
        isPlaying={false}
        onTogglePlayback={vi.fn()}
        onStepFrame={vi.fn()}
        playbackSpeed={1}
        onSetPlaybackSpeed={vi.fn()}
        autoPlay={false}
        onSetAutoPlay={vi.fn()}
        autoLoop={false}
        onSetAutoLoop={vi.fn()}
        playbackRangeStart={0}
        playbackRangeEnd={79}
        onSetFrameWithinPlaybackRange={vi.fn()}
        playbackRangeHighlight={null}
        playbackRangeLabel={null}
      />,
    )

    expect(screen.getByRole('status')).toHaveTextContent('Loading episode…')
  })

  it('does not show video loading overlay before 200ms delay', () => {
    renderPlaybackCard({
      videoSrc: '/api/datasets/test/episodes/0/video/wrist',
      frameImageUrl: null,
    })

    expect(screen.queryByText('Loading video…')).not.toBeInTheDocument()
  })

  it('shows video loading overlay after 200ms when video has not loaded', () => {
    renderPlaybackCard({
      videoSrc: '/api/datasets/test/episodes/0/video/wrist',
      frameImageUrl: null,
    })

    act(() => {
      vi.advanceTimersByTime(200)
    })

    expect(screen.getByText('Loading video…')).toBeInTheDocument()
  })

  it('hides video loading overlay after loadedmetadata fires', () => {
    renderPlaybackCard({
      videoSrc: '/api/datasets/test/episodes/0/video/wrist',
      frameImageUrl: null,
    })

    act(() => {
      vi.advanceTimersByTime(200)
    })

    expect(screen.getByText('Loading video…')).toBeInTheDocument()

    const video = document.querySelector('video')!
    fireEvent.loadedMetadata(video)

    expect(screen.queryByText('Loading video…')).not.toBeInTheDocument()
  })

  it('does not show video loading overlay when video loads within 200ms', () => {
    renderPlaybackCard({
      videoSrc: '/api/datasets/test/episodes/0/video/wrist',
      frameImageUrl: null,
    })

    const video = document.querySelector('video')!
    fireEvent.loadedMetadata(video)

    act(() => {
      vi.advanceTimersByTime(200)
    })

    expect(screen.queryByText('Loading video…')).not.toBeInTheDocument()
  })
  it('names playback position and exposes playback toggle states', () => {
    renderPlaybackCard({ compact: true, currentFrame: 12, autoPlay: true, autoLoop: false })

    expect(screen.getByRole('slider', { name: 'Playback frame' })).toHaveAttribute(
      'aria-valuetext',
      'Frame 13 of 100',
    )
    expect(screen.getByRole('button', { name: 'Auto-play' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByRole('button', { name: 'Loop playback' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
  })
})
