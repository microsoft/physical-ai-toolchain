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

  it('renders interpolated frame image when isInsertedFrame and interpolatedImageUrl are set', () => {
    renderPlaybackCard({
      videoSrc: null,
      frameImageUrl: null,
      isInsertedFrame: true,
      interpolatedImageUrl: 'data:image/jpeg;base64,abc',
      currentFrame: 5,
    })

    expect(screen.getByAltText('Interpolated frame 5')).toBeInTheDocument()
    expect(screen.getByText('Interpolated Frame')).toBeInTheDocument()
  })

  it('renders frame counter fallback when no media source is available', () => {
    renderPlaybackCard({
      videoSrc: null,
      frameImageUrl: null,
      currentFrame: 7,
      totalFrames: 50,
    })

    expect(screen.getByText('Frame 8 of 50')).toBeInTheDocument()
  })

  it('renders resize output badge when provided', () => {
    renderPlaybackCard({
      resizeOutput: { width: 320, height: 240 },
    })

    expect(screen.getByText('Output: 320 × 240')).toBeInTheDocument()
  })

  it('applies displayFilter inline style to frame image', () => {
    renderPlaybackCard({
      videoSrc: null,
      frameImageUrl: '/api/datasets/test/episodes/0/frames/0?camera=wrist',
      displayFilter: 'brightness(1.2)',
    })

    const img = screen.getByAltText('Frame 0')
    expect(img).toHaveStyle({ filter: 'brightness(1.2)' })
  })

  it('renders playback range highlight and label when provided', () => {
    renderPlaybackCard({
      playbackRangeHighlight: { left: '10%', width: '40%' },
      playbackRangeLabel: 'Active subtask',
      playbackRangeStart: 10,
      playbackRangeEnd: 50,
    })

    expect(screen.getByText('Active subtask: frames 10 to 50')).toBeInTheDocument()
  })

  it('fires onSetFrameWithinPlaybackRange when slider changes', () => {
    const onSetFrameWithinPlaybackRange = vi.fn()
    renderPlaybackCard({ onSetFrameWithinPlaybackRange })

    const slider = document.querySelector('input[type="range"]')!
    fireEvent.change(slider, { target: { value: '42' } })

    expect(onSetFrameWithinPlaybackRange).toHaveBeenCalledWith(42)
  })

  describe('default controls', () => {
    it('fires callbacks on play/step/reset/autoplay/autoloop buttons', () => {
      const onTogglePlayback = vi.fn()
      const onStepFrame = vi.fn()
      const onSetFrameWithinPlaybackRange = vi.fn()
      const onSetAutoPlay = vi.fn()
      const onSetAutoLoop = vi.fn()

      renderPlaybackCard({
        compact: false,
        playbackRangeStart: 5,
        autoPlay: false,
        autoLoop: false,
        onTogglePlayback,
        onStepFrame,
        onSetFrameWithinPlaybackRange,
        onSetAutoPlay,
        onSetAutoLoop,
      })

      fireEvent.click(screen.getByRole('button', { name: 'Play' }))
      fireEvent.click(screen.getByTitle('Previous frame'))
      fireEvent.click(screen.getByTitle('Next frame'))
      const resetButtons = screen
        .getAllByRole('button')
        .filter((btn) => btn.querySelector('svg.lucide-rotate-ccw'))
      fireEvent.click(resetButtons[0])
      fireEvent.click(screen.getByRole('button', { name: /^Auto$/i }))
      fireEvent.click(screen.getByRole('button', { name: /^Loop$/i }))

      expect(onTogglePlayback).toHaveBeenCalled()
      expect(onStepFrame).toHaveBeenCalledWith(-1)
      expect(onStepFrame).toHaveBeenCalledWith(1)
      expect(onSetFrameWithinPlaybackRange).toHaveBeenCalledWith(5)
      expect(onSetAutoPlay).toHaveBeenCalledWith(true)
      expect(onSetAutoLoop).toHaveBeenCalledWith(true)
    })
  })

  describe('compact controls', () => {
    it('renders compact controls and fires callbacks on every button', () => {
      const onTogglePlayback = vi.fn()
      const onStepFrame = vi.fn()
      const onSetFrameWithinPlaybackRange = vi.fn()
      const onSetAutoPlay = vi.fn()
      const onSetAutoLoop = vi.fn()

      renderPlaybackCard({
        compact: true,
        isPlaying: false,
        autoPlay: true,
        autoLoop: true,
        playbackRangeStart: 3,
        onTogglePlayback,
        onStepFrame,
        onSetFrameWithinPlaybackRange,
        onSetAutoPlay,
        onSetAutoLoop,
      })

      expect(screen.getByTestId('trajectory-compact-controls')).toBeInTheDocument()
      fireEvent.click(screen.getByLabelText('Play playback'))
      fireEvent.click(screen.getByLabelText('Previous frame'))
      fireEvent.click(screen.getByLabelText('Next frame'))
      fireEvent.click(screen.getByLabelText('Reset playback'))
      fireEvent.click(screen.getByLabelText('Toggle auto-play'))
      fireEvent.click(screen.getByLabelText('Toggle loop playback'))

      expect(onTogglePlayback).toHaveBeenCalled()
      expect(onStepFrame).toHaveBeenCalledWith(-1)
      expect(onStepFrame).toHaveBeenCalledWith(1)
      expect(onSetFrameWithinPlaybackRange).toHaveBeenCalledWith(3)
      expect(onSetAutoPlay).toHaveBeenCalledWith(false)
      expect(onSetAutoLoop).toHaveBeenCalledWith(false)
    })
  })

  it('fires onSelectCamera when camera selector changes', () => {
    const onSelectCamera = vi.fn()
    renderPlaybackCard({
      cameras: ['wrist', 'overhead'],
      selectedCamera: 'wrist',
      onSelectCamera,
    })

    const select = document.querySelector('select')
    if (select) {
      fireEvent.change(select, { target: { value: 'overhead' } })
      expect(onSelectCamera).toHaveBeenCalledWith('overhead')
    }
  })
})
