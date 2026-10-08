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
