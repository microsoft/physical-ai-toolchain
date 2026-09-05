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
    selectedCameras: ['wrist'],
    onSelectionChange: vi.fn(),
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

  it('renders selected cameras side by side', () => {
    renderPlaybackCard({
      cameras: ['wrist', 'front'],
      selectedCamera: 'wrist',
      selectedCameras: ['wrist', 'front'],
      videoSrc: '/videos/wrist.mp4',
      videoUrls: { wrist: '/videos/wrist.mp4', front: '/videos/front.mp4' },
      frameImageUrl: null,
    })

    expect(screen.getByLabelText('Wrist camera view')).toBeVisible()
    expect(screen.getByLabelText('Front camera view')).toBeVisible()
    expect(screen.getByTestId('trajectory-camera-grid')).toHaveClass('grid-cols-2')
  })

  it('shows the end-effector 3D view alongside camera playback', () => {
    renderPlaybackCard({
      cameras: ['wrist', 'front'],
      selectedCameras: ['wrist'],
      videoSrc: '/videos/wrist.mp4',
      videoUrls: { wrist: '/videos/wrist.mp4', front: '/videos/front.mp4' },
      frameImageUrl: null,
      endEffectorTrajectories: [
        {
          id: 'end-effector',
          label: 'End effector',
          points: [[0, 0, 0]],
          lineColor: '#06b6d4',
          markerColor: '#f43f5e',
        },
      ],
    })

    fireEvent.click(screen.getByRole('button', { name: /1 camera/i }))
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: 'End effector 3D' }))

    expect(screen.getByRole('region', { name: 'End-effector trajectory' })).toBeVisible()
    expect(screen.getByLabelText('Wrist camera view')).toBeVisible()
    expect(screen.getByTestId('trajectory-camera-grid')).toHaveClass('grid-cols-2')
    expect(screen.getByTestId('trajectory-camera-grid')).toHaveAttribute('data-view-count', '2')

    fireEvent.click(screen.getByRole('button', { name: /1 camera.*3D/i }))
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: 'End effector 3D' }))

    expect(
      screen.queryByRole('region', { name: 'End-effector trajectory' }),
    ).not.toBeInTheDocument()
    expect(screen.getByLabelText('Wrist camera view')).toBeVisible()
  })

  it('keeps two cameras and the 3D view in one visible row', () => {
    renderPlaybackCard({
      compact: true,
      cameras: ['wrist', 'front'],
      selectedCameras: ['wrist', 'front'],
      videoSrc: '/videos/wrist.mp4',
      videoUrls: { wrist: '/videos/wrist.mp4', front: '/videos/front.mp4' },
      frameImageUrl: null,
      endEffectorTrajectories: [
        {
          id: 'end-effector',
          label: 'End effector',
          points: [[0, 0, 0]],
          lineColor: '#06b6d4',
          markerColor: '#f43f5e',
        },
      ],
    })

    fireEvent.click(screen.getByRole('button', { name: /2 cameras/i }))
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: 'End effector 3D' }))

    const frame = screen.getByTestId('trajectory-compact-media-frame')
    expect(frame).toHaveAttribute('data-view-count', '3')
    expect(frame).toHaveClass('grid-cols-3')
    expect(screen.getByLabelText('Wrist camera view')).toBeVisible()
    expect(screen.getByLabelText('Front camera view')).toBeVisible()
    expect(screen.getByRole('region', { name: 'End-effector trajectory' })).toBeVisible()
  })

  it('expands compact trajectory media by default and allows collapsing it', () => {
    renderPlaybackCard({
      compact: true,
      cameras: ['wrist', 'front'],
      selectedCameras: ['wrist', 'front'],
      videoSrc: '/videos/wrist.mp4',
      videoUrls: { wrist: '/videos/wrist.mp4', front: '/videos/front.mp4' },
      frameImageUrl: null,
    })

    const card = screen.getByTestId('trajectory-playback-card')
    const frame = screen.getByTestId('trajectory-compact-media-frame')
    expect(card).not.toHaveClass('max-w-[44rem]')
    expect(frame).toHaveClass('max-w-none')
    expect(frame).toHaveClass('max-h-[34rem]')

    fireEvent.click(screen.getByRole('button', { name: 'Compact media' }))

    expect(card).toHaveClass('max-w-[44rem]')
    expect(frame).toHaveClass('max-w-[40rem]')
    expect(frame).toHaveClass('max-h-[18rem]')
    expect(screen.getByRole('button', { name: 'Expand media' })).toBeVisible()
  })
})
