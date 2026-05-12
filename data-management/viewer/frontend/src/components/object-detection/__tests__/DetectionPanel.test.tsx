import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Detection, DetectionFilters, EpisodeDetectionSummary } from '@/types/detection'

import { DetectionPanel } from '../DetectionPanel'

const useDetectionPanelStateMock = vi.fn()

vi.mock('../useDetectionPanelState', () => ({
  useDetectionPanelState: () => useDetectionPanelStateMock(),
}))

vi.mock('../DetectionPanelResultsColumn', () => ({
  DetectionPanelResultsColumn: (props: Record<string, unknown>) => (
    <div
      data-testid="results-column"
      data-current-frame={String(props.currentFrame)}
      data-error-message={props.error instanceof Error ? props.error.message : ''}
      data-error-is-error={props.error instanceof Error ? 'true' : 'false'}
      data-has-data={props.data ? 'true' : 'false'}
      data-image-url={String(props.imageUrl ?? '')}
      data-is-loading={String(props.isLoading)}
      data-is-playing={String(props.isPlaying)}
      data-is-running={String(props.isRunning)}
      data-needs-rerun={String(props.needsRerun)}
      data-playback-speed={String(props.playbackSpeed)}
      data-progress={String(props.progress)}
      data-total-frames={String(props.totalFrames)}
    />
  ),
}))

vi.mock('../DetectionPanelSidebar', () => ({
  DetectionPanelSidebar: (props: Record<string, unknown>) => (
    <div
      data-testid="sidebar"
      data-classes={Array.isArray(props.availableClasses) ? props.availableClasses.join(',') : ''}
      data-current-frame={String(props.currentFrame)}
      data-has-data={props.data ? 'true' : 'false'}
      data-min-confidence={String((props.filters as DetectionFilters).minConfidence)}
    />
  ),
}))

const buildDetection = (overrides: Partial<Detection> = {}): Detection => ({
  class_id: 0,
  class_name: 'person',
  confidence: 0.9,
  bbox: [10, 20, 30, 40],
  ...overrides,
})

const buildSummary = (
  overrides: Partial<EpisodeDetectionSummary> = {},
): EpisodeDetectionSummary => ({
  total_frames: 100,
  processed_frames: 100,
  total_detections: 5,
  detections_by_frame: [],
  class_summary: {},
  ...overrides,
})

const baseFilters: DetectionFilters = { classes: [], minConfidence: 0.25 }

const baseState = {
  availableClasses: ['person', 'car'],
  currentDataset: { id: 'ds-1' } as { id: string } | null,
  currentDetections: [] as Detection[],
  currentEpisode: { meta: { index: 0, length: 100 } } as {
    meta: { index: number; length: number }
  } | null,
  currentFrame: 0,
  data: null as EpisodeDetectionSummary | null,
  error: null as unknown,
  filteredData: null as EpisodeDetectionSummary | null,
  filters: baseFilters,
  imageUrl: '/api/img.jpg',
  isLoading: false,
  isPlaying: false,
  isRunning: false,
  needsRerun: false,
  playbackSpeed: 1,
  progress: 0,
  runDetection: vi.fn(),
  setCurrentFrame: vi.fn(),
  setFilters: vi.fn(),
  setPlaybackSpeed: vi.fn(),
  togglePlayback: vi.fn(),
  totalFrames: 100,
}

const setState = (overrides: Partial<typeof baseState> = {}) => {
  useDetectionPanelStateMock.mockReturnValue({ ...baseState, ...overrides })
}

beforeEach(() => {
  setState()
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('DetectionPanel', () => {
  describe('empty state', () => {
    it('renders the "No Episode Selected" card when no dataset is loaded', () => {
      setState({ currentDataset: null })

      render(<DetectionPanel />)

      expect(screen.getByText('No Episode Selected')).toBeInTheDocument()
      expect(
        screen.getByText('Select a dataset and episode from the sidebar to run object detection.'),
      ).toBeInTheDocument()
      expect(screen.queryByTestId('results-column')).not.toBeInTheDocument()
      expect(screen.queryByTestId('sidebar')).not.toBeInTheDocument()
    })

    it('renders the "No Episode Selected" card when no episode is loaded', () => {
      setState({ currentEpisode: null })

      render(<DetectionPanel />)

      expect(screen.getByText('No Episode Selected')).toBeInTheDocument()
      expect(screen.queryByTestId('results-column')).not.toBeInTheDocument()
      expect(screen.queryByTestId('sidebar')).not.toBeInTheDocument()
    })

    it('renders the empty card when both dataset and episode are missing', () => {
      setState({ currentDataset: null, currentEpisode: null })

      render(<DetectionPanel />)

      expect(screen.getByText('No Episode Selected')).toBeInTheDocument()
    })
  })

  describe('main layout', () => {
    it('renders the results column and sidebar when a dataset and episode are loaded', () => {
      render(<DetectionPanel />)

      expect(screen.getByTestId('results-column')).toBeInTheDocument()
      expect(screen.getByTestId('sidebar')).toBeInTheDocument()
      expect(screen.queryByText('No Episode Selected')).not.toBeInTheDocument()
    })

    it('forwards detection state to the results column', () => {
      const data = buildSummary({ total_detections: 12 })
      const filteredData = buildSummary({ total_detections: 8 })
      setState({
        currentFrame: 7,
        data,
        filteredData,
        imageUrl: '/api/frame-7.jpg',
        isLoading: true,
        isPlaying: true,
        isRunning: false,
        needsRerun: true,
        playbackSpeed: 2,
        progress: 42,
        totalFrames: 250,
      })

      render(<DetectionPanel />)

      const column = screen.getByTestId('results-column')
      expect(column).toHaveAttribute('data-current-frame', '7')
      expect(column).toHaveAttribute('data-has-data', 'true')
      expect(column).toHaveAttribute('data-image-url', '/api/frame-7.jpg')
      expect(column).toHaveAttribute('data-is-loading', 'true')
      expect(column).toHaveAttribute('data-is-playing', 'true')
      expect(column).toHaveAttribute('data-is-running', 'false')
      expect(column).toHaveAttribute('data-needs-rerun', 'true')
      expect(column).toHaveAttribute('data-playback-speed', '2')
      expect(column).toHaveAttribute('data-progress', '42')
      expect(column).toHaveAttribute('data-total-frames', '250')
    })

    it('forwards filter state and class list to the sidebar', () => {
      setState({
        availableClasses: ['person', 'car', 'dog'],
        currentFrame: 12,
        data: buildSummary(),
        filters: { classes: ['person'], minConfidence: 0.65 },
      })

      render(<DetectionPanel />)

      const sidebar = screen.getByTestId('sidebar')
      expect(sidebar).toHaveAttribute('data-classes', 'person,car,dog')
      expect(sidebar).toHaveAttribute('data-current-frame', '12')
      expect(sidebar).toHaveAttribute('data-has-data', 'true')
      expect(sidebar).toHaveAttribute('data-min-confidence', '0.65')
    })
  })

  describe('error coercion', () => {
    it('passes Error instances through to the results column', () => {
      const error = new Error('detection failed')
      setState({ error })

      render(<DetectionPanel />)

      const column = screen.getByTestId('results-column')
      expect(column).toHaveAttribute('data-error-is-error', 'true')
      expect(column).toHaveAttribute('data-error-message', 'detection failed')
    })

    it('coerces non-Error values to null before passing to the results column', () => {
      setState({ error: 'string error value' })

      render(<DetectionPanel />)

      const column = screen.getByTestId('results-column')
      expect(column).toHaveAttribute('data-error-is-error', 'false')
      expect(column).toHaveAttribute('data-error-message', '')
    })

    it('passes null through unchanged when no error is present', () => {
      setState({ error: null })

      render(<DetectionPanel />)

      const column = screen.getByTestId('results-column')
      expect(column).toHaveAttribute('data-error-is-error', 'false')
    })
  })

  describe('detection passthrough', () => {
    it('renders both children even when current detections are present without filtered data', () => {
      setState({
        currentDetections: [buildDetection({ class_name: 'cat' })],
        data: buildSummary(),
      })

      render(<DetectionPanel />)

      expect(screen.getByTestId('results-column')).toBeInTheDocument()
      expect(screen.getByTestId('sidebar')).toBeInTheDocument()
    })
  })
})
