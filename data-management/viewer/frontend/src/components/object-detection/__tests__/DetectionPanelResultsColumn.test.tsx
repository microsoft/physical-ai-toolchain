import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Detection, DetectionFilters, EpisodeDetectionSummary } from '@/types/detection'

import { DetectionPanelResultsColumn } from '../DetectionPanelResultsColumn'

vi.mock('../DetectionViewer', () => ({
  DetectionViewer: ({
    imageUrl,
    detections,
  }: {
    imageUrl: string | null
    detections: Detection[]
  }) => (
    <div
      data-testid="detection-viewer"
      data-image-url={imageUrl ?? 'null'}
      data-count={detections.length}
    />
  ),
}))

vi.mock('../DetectionTimeline', () => ({
  DetectionTimeline: ({
    totalFrames,
    currentFrame,
    onFrameClick,
  }: {
    totalFrames: number
    currentFrame: number
    onFrameClick: (frame: number) => void
  }) => (
    <button
      type="button"
      data-testid="detection-timeline"
      data-total={totalFrames}
      data-current={currentFrame}
      onClick={() => onFrameClick(7)}
    />
  ),
}))

vi.mock('../DetectionCharts', () => ({
  DetectionCharts: ({ summary }: { summary: EpisodeDetectionSummary }) => (
    <div data-testid="detection-charts" data-total={summary.total_detections} />
  ),
}))

vi.mock('@/components/playback/PlaybackControlStrip', () => ({
  PlaybackControlStrip: ({
    controls,
    slider,
    currentFrame,
    totalFrames,
  }: {
    controls: React.ReactNode
    slider: React.ReactNode
    currentFrame: number
    totalFrames: number
  }) => (
    <div data-testid="playback-strip" data-current={currentFrame} data-total={totalFrames}>
      <div data-testid="playback-controls">{controls}</div>
      <div data-testid="playback-slider">{slider}</div>
    </div>
  ),
}))

const buildSummary = (
  overrides: Partial<EpisodeDetectionSummary> = {},
): EpisodeDetectionSummary => ({
  total_frames: 100,
  processed_frames: 50,
  total_detections: 200,
  detections_by_frame: [],
  class_summary: {},
  ...overrides,
})

const buildDetection = (overrides: Partial<Detection> = {}): Detection => ({
  class_id: 0,
  class_name: 'person',
  confidence: 0.9,
  bbox: [0, 0, 10, 10],
  ...overrides,
})

const defaultFilters: DetectionFilters = { classes: [], minConfidence: 0.4 }

const renderColumn = (
  props: Partial<React.ComponentProps<typeof DetectionPanelResultsColumn>> = {},
) => {
  const runDetection = vi.fn()
  const setCurrentFrame = vi.fn()
  const setPlaybackSpeed = vi.fn()
  const togglePlayback = vi.fn()
  const utils = render(
    <DetectionPanelResultsColumn
      currentDetections={props.currentDetections ?? []}
      currentFrame={props.currentFrame ?? 0}
      data={props.data ?? null}
      error={props.error ?? null}
      filteredData={props.filteredData ?? null}
      imageUrl={props.imageUrl ?? null}
      isLoading={props.isLoading ?? false}
      isPlaying={props.isPlaying ?? false}
      isRunning={props.isRunning ?? false}
      needsRerun={props.needsRerun ?? false}
      playbackSpeed={props.playbackSpeed ?? 1}
      progress={props.progress ?? 0}
      filters={props.filters ?? defaultFilters}
      runDetection={props.runDetection ?? runDetection}
      setCurrentFrame={props.setCurrentFrame ?? setCurrentFrame}
      setPlaybackSpeed={props.setPlaybackSpeed ?? setPlaybackSpeed}
      togglePlayback={props.togglePlayback ?? togglePlayback}
      totalFrames={props.totalFrames ?? 50}
    />,
  )
  return { ...utils, runDetection, setCurrentFrame, setPlaybackSpeed, togglePlayback }
}

describe('DetectionPanelResultsColumn', () => {
  describe('Run button', () => {
    it('labels the button "Run Detection" before any detection has run', () => {
      renderColumn({ data: null })
      expect(screen.getByRole('button', { name: 'Run Detection' })).toBeInTheDocument()
    })

    it('labels the button "Run Again" once detection results exist', () => {
      renderColumn({ data: buildSummary() })
      expect(screen.getByRole('button', { name: 'Run Again' })).toBeInTheDocument()
    })

    it('labels the button "Re-run Detection" when a re-run is needed', () => {
      renderColumn({ data: buildSummary(), needsRerun: true })
      expect(screen.getByRole('button', { name: 'Re-run Detection' })).toBeInTheDocument()
    })

    it('shows the spinner label "Detecting..." when isRunning', () => {
      renderColumn({ isRunning: true })
      expect(screen.getByRole('button', { name: 'Detecting...' })).toBeInTheDocument()
    })

    it('disables the button when isRunning', () => {
      renderColumn({ isRunning: true })
      expect(screen.getByRole('button', { name: 'Detecting...' })).toBeDisabled()
    })

    it('disables the button when isLoading', () => {
      renderColumn({ isLoading: true })
      expect(screen.getByRole('button', { name: 'Run Detection' })).toBeDisabled()
    })

    it('invokes runDetection with the current minConfidence when clicked', () => {
      const { runDetection } = renderColumn({
        filters: { classes: [], minConfidence: 0.42 },
      })
      fireEvent.click(screen.getByRole('button', { name: 'Run Detection' }))
      expect(runDetection).toHaveBeenCalledWith({ confidence: 0.42 })
    })
  })

  describe('Status banners', () => {
    it('shows the "Edits detected" badge when needsRerun is true and data exists', () => {
      renderColumn({ data: buildSummary(), needsRerun: true })
      expect(screen.getByText('Edits detected')).toBeInTheDocument()
    })

    it('hides the "Edits detected" badge when there is no data yet', () => {
      renderColumn({ data: null, needsRerun: true })
      expect(screen.queryByText('Edits detected')).not.toBeInTheDocument()
    })

    it('renders the progress bar with rounded percentage when isRunning', () => {
      renderColumn({ isRunning: true, progress: 42.7, totalFrames: 100 })
      expect(screen.getByText('Processing 100 frames...')).toBeInTheDocument()
      expect(screen.getByText('43%')).toBeInTheDocument()
    })

    it('renders the error message when error is set', () => {
      renderColumn({ error: new Error('boom') })
      expect(screen.getByText('Error:')).toBeInTheDocument()
      expect(screen.getByText('boom')).toBeInTheDocument()
    })
  })

  describe('Viewer placeholder states', () => {
    it('shows the "No detection results" placeholder when no data and not running', () => {
      renderColumn({ data: null, isRunning: false })
      expect(screen.getByText('No detection results')).toBeInTheDocument()
      expect(screen.queryByTestId('detection-viewer')).not.toBeInTheDocument()
    })

    it('shows the "Processing frames..." placeholder when running with no data yet', () => {
      renderColumn({ data: null, isRunning: true })
      expect(screen.getByText('Processing frames...')).toBeInTheDocument()
      expect(screen.queryByTestId('detection-viewer')).not.toBeInTheDocument()
    })

    it('renders DetectionViewer with the imageUrl and current detections when data exists', () => {
      renderColumn({
        data: buildSummary(),
        imageUrl: '/frame.png',
        currentDetections: [buildDetection(), buildDetection({ class_name: 'car' })],
      })
      const viewer = screen.getByTestId('detection-viewer')
      expect(viewer).toHaveAttribute('data-image-url', '/frame.png')
      expect(viewer).toHaveAttribute('data-count', '2')
    })
  })

  describe('Playback controls', () => {
    it('renders the playback strip with current frame and total frames when data exists', () => {
      renderColumn({ data: buildSummary(), totalFrames: 25, currentFrame: 5 })
      const strip = screen.getByTestId('playback-strip')
      expect(strip).toHaveAttribute('data-current', '5')
      expect(strip).toHaveAttribute('data-total', '25')
    })

    it('shows the Pause label when isPlaying is true', () => {
      renderColumn({ data: buildSummary(), isPlaying: true })
      expect(screen.getByRole('button', { name: /Pause/ })).toBeInTheDocument()
    })

    it('shows the Play label when isPlaying is false', () => {
      renderColumn({ data: buildSummary(), isPlaying: false })
      expect(screen.getByRole('button', { name: /Play/ })).toBeInTheDocument()
    })

    it('invokes togglePlayback when the play button is clicked', () => {
      const { togglePlayback } = renderColumn({ data: buildSummary() })
      fireEvent.click(screen.getByRole('button', { name: /Play/ }))
      expect(togglePlayback).toHaveBeenCalledTimes(1)
    })

    it('resets the current frame to 0 when the rewind button is clicked', () => {
      const { setCurrentFrame } = renderColumn({
        data: buildSummary(),
        currentFrame: 10,
      })
      const buttons = screen.getAllByRole('button')
      const rewindButton = buttons.find((b) => b.querySelector('svg.lucide-rotate-ccw'))
      expect(rewindButton).toBeDefined()
      fireEvent.click(rewindButton!)
      expect(setCurrentFrame).toHaveBeenCalledWith(0)
    })

    it('selects a new playback speed when a speed button is clicked', () => {
      const { setPlaybackSpeed } = renderColumn({ data: buildSummary(), playbackSpeed: 1 })
      fireEvent.click(screen.getByRole('button', { name: '2x' }))
      expect(setPlaybackSpeed).toHaveBeenCalledWith(2)
    })

    it('updates the current frame from the slider input', () => {
      const { setCurrentFrame } = renderColumn({
        data: buildSummary(),
        totalFrames: 50,
      })
      const slider = screen.getByRole('slider') as HTMLInputElement
      fireEvent.change(slider, { target: { value: '12' } })
      expect(setCurrentFrame).toHaveBeenCalledWith(12)
    })
  })

  describe('Timeline and Statistics cards', () => {
    it('does not render the Detection Timeline card when there is no data', () => {
      renderColumn({ data: null })
      expect(screen.queryByText('Detection Timeline')).not.toBeInTheDocument()
    })

    it('renders the Detection Timeline card with singular caption when one detection', () => {
      renderColumn({
        data: buildSummary(),
        filteredData: buildSummary(),
        currentDetections: [buildDetection()],
        currentFrame: 3,
      })
      expect(screen.getByText('Detection Timeline')).toBeInTheDocument()
      expect(screen.getByText('Frame 3 - 1 detection')).toBeInTheDocument()
    })

    it('renders the Detection Timeline card with plural caption when many detections', () => {
      renderColumn({
        data: buildSummary(),
        filteredData: buildSummary(),
        currentDetections: [buildDetection(), buildDetection({ class_name: 'car' })],
        currentFrame: 4,
      })
      expect(screen.getByText('Frame 4 - 2 detections')).toBeInTheDocument()
    })

    it('forwards a frame click from DetectionTimeline to setCurrentFrame', () => {
      const { setCurrentFrame } = renderColumn({
        data: buildSummary(),
        filteredData: buildSummary(),
      })
      fireEvent.click(screen.getByTestId('detection-timeline'))
      expect(setCurrentFrame).toHaveBeenCalledWith(7)
    })

    it('renders the Detection Statistics card with charts when data and filteredData exist', () => {
      renderColumn({
        data: buildSummary(),
        filteredData: buildSummary({ total_detections: 9 }),
      })
      expect(screen.getByText('Detection Statistics')).toBeInTheDocument()
      expect(screen.getByTestId('detection-charts')).toHaveAttribute('data-total', '9')
    })

    it('does not render the Detection Statistics card when filteredData is null', () => {
      renderColumn({ data: buildSummary(), filteredData: null })
      expect(screen.queryByText('Detection Statistics')).not.toBeInTheDocument()
    })
  })
})
