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

    expect(screen.getByText('Loading episode…')).toBeInTheDocument()
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

    expect(screen.getByText('Loading episode…')).toBeInTheDocument()
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
})
