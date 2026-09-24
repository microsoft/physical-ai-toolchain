import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

  it('mounts only selected cameras with full preload in the camera grid', () => {
    renderPlaybackCard({
      videoSrc: '/videos/wrist.mp4',
      videoUrls: {
        wrist: '/videos/wrist.mp4',
        overhead: '/videos/overhead.mp4',
        side: '/videos/side.mp4',
      },
      cameras: ['wrist', 'overhead', 'side'],
      selectedCamera: 'wrist',
      selectedCameras: ['wrist', 'overhead'],
      onSelectionChange: vi.fn(),
      frameImageUrl: null,
    })

    const videos = Array.from(document.querySelectorAll('video'))
    expect(videos.map((video) => video.dataset.camera)).toEqual(['wrist', 'overhead'])
    expect(videos.every((video) => video.preload === 'auto')).toBe(true)
    expect(screen.getByTestId('trajectory-camera-grid')).toHaveAttribute('data-camera-count', '2')
  })

  it('corrects secondary camera drift using each camera time window', () => {
    const videoRef = createRef<HTMLVideoElement>()
    const { rerender } = renderPlaybackCard({
      videoRef,
      videoSrc: '/videos/wrist.mp4',
      videoUrls: { wrist: '/videos/wrist.mp4', overhead: '/videos/overhead.mp4' },
      cameras: ['wrist', 'overhead'],
      selectedCamera: 'wrist',
      selectedCameras: ['wrist', 'overhead'],
      onSelectionChange: vi.fn(),
      videoWindows: { wrist: [10, 20], overhead: [30, 40] },
      datasetFps: 20,
      frameImageUrl: null,
    })
    const secondary = document.querySelector<HTMLVideoElement>('video[data-camera="overhead"]')!
    videoRef.current!.currentTime = 12
    secondary.currentTime = 30

    rerender(
      <AnnotationWorkspacePlaybackCard
        compact={false}
        canvasRef={createRef<HTMLCanvasElement>()}
        videoRef={videoRef}
        videoSrc="/videos/wrist.mp4"
        videoUrls={{ wrist: '/videos/wrist.mp4', overhead: '/videos/overhead.mp4' }}
        videoWindows={{ wrist: [10, 20], overhead: [30, 40] }}
        datasetFps={20}
        onVideoEnded={vi.fn()}
        onLoadedMetadata={vi.fn()}
        isInsertedFrame={false}
        interpolatedImageUrl={null}
        currentFrame={1}
        totalFrames={100}
        resizeOutput={null}
        frameImageUrl={null}
        cameras={['wrist', 'overhead']}
        selectedCamera="wrist"
        selectedCameras={['wrist', 'overhead']}
        onSelectCamera={vi.fn()}
        onSelectionChange={vi.fn()}
        isPlaying={false}
        onTogglePlayback={vi.fn()}
        onStepFrame={vi.fn()}
        playbackSpeed={1.5}
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

    expect(secondary.currentTime).toBe(32)
    expect(secondary.playbackRate).toBe(1.5)
  })

  it('offers compact and expanded media grid states', async () => {
    vi.useRealTimers()
    const user = userEvent.setup()
    renderPlaybackCard({ compact: true })

    const card = screen.getByTestId('trajectory-playback-card')
    await user.click(screen.getByRole('button', { name: 'Expand media' }))

    expect(screen.getByRole('button', { name: 'Compact media' })).toBeInTheDocument()
    expect(card).not.toHaveClass('max-w-[44rem]')
  })

  it('toggles a bounded analysis view with an explicit unsupported-data state', async () => {
    vi.useRealTimers()
    const user = userEvent.setup()
    renderPlaybackCard({
      selectedCameras: ['wrist'],
      onSelectionChange: vi.fn(),
      endEffectorTrajectories: [],
    })

    await user.click(screen.getByRole('button', { name: '1 camera' }))
    await user.click(screen.getByRole('menuitemcheckbox', { name: 'End effector 3D' }))

    expect(screen.getByTestId('trajectory-camera-grid')).toHaveAttribute('data-view-count', '2')
    expect(screen.getByRole('region', { name: 'End-effector trajectory analysis' })).toBeVisible()
    expect(
      screen.getByText('Analysis only · not calibration, clearance, or physical accuracy'),
    ).toBeVisible()
    expect(screen.getByText('No supported recorded trajectory data')).toBeVisible()
  })
})
