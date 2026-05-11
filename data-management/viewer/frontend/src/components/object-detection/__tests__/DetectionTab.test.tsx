import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useObjectDetection } from '@/hooks/use-object-detection'
import { useDatasetStore, useEpisodeStore, usePlaybackControls } from '@/stores'
import type { Detection, DetectionFilters, EpisodeDetectionSummary } from '@/types/detection'

import { DetectionTab } from '../DetectionTab'

vi.mock('@/hooks/use-object-detection', () => ({
  useObjectDetection: vi.fn(),
}))

vi.mock('@/stores', () => ({
  useDatasetStore: vi.fn(),
  useEpisodeStore: vi.fn(),
  usePlaybackControls: vi.fn(),
}))

vi.mock('../DetectionViewer', () => ({
  DetectionViewer: (props: { imageUrl: string | null; detections: Detection[] }) => (
    <div
      data-testid="detection-viewer"
      data-image-url={String(props.imageUrl ?? '')}
      data-detection-count={String(props.detections.length)}
    />
  ),
}))

vi.mock('../DetectionTimeline', () => ({
  DetectionTimeline: (props: {
    detectionsPerFrame: { frame: number }[]
    totalFrames: number
    currentFrame: number
  }) => (
    <div
      data-testid="detection-timeline"
      data-current-frame={String(props.currentFrame)}
      data-total-frames={String(props.totalFrames)}
      data-frame-count={String(props.detectionsPerFrame.length)}
    />
  ),
}))

vi.mock('../DetectionFilters', () => ({
  DetectionFilters: (props: {
    filters: DetectionFilters
    availableClasses: string[]
    onFiltersChange: (next: DetectionFilters) => void
  }) => (
    <div
      data-testid="detection-filters"
      data-min-confidence={String(props.filters.minConfidence)}
      data-classes={props.availableClasses.join(',')}
    />
  ),
}))

vi.mock('../DetectionCharts', () => ({
  DetectionCharts: (props: { summary: EpisodeDetectionSummary }) => (
    <div data-testid="detection-charts" data-total={String(props.summary.total_detections)} />
  ),
}))

const mockedUseObjectDetection = vi.mocked(useObjectDetection)
const mockedUseDatasetStore = vi.mocked(useDatasetStore)
const mockedUseEpisodeStore = vi.mocked(useEpisodeStore)
const mockedUsePlaybackControls = vi.mocked(usePlaybackControls)

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
  class_summary: { person: { count: 5, avg_confidence: 0.85 } },
  ...overrides,
})

interface SetupOptions {
  dataset?: { id: string } | null
  episode?: { meta: { index: number; length: number } } | null
  currentFrame?: number
  setCurrentFrame?: ReturnType<typeof vi.fn>
  data?: EpisodeDetectionSummary | null
  filteredData?: EpisodeDetectionSummary | null
  isLoading?: boolean
  isRunning?: boolean
  error?: unknown
  needsRerun?: boolean
  filters?: DetectionFilters
  setFilters?: ReturnType<typeof vi.fn>
  runDetection?: ReturnType<typeof vi.fn>
  availableClasses?: string[]
}

function setup(opts: SetupOptions = {}) {
  const dataset = opts.dataset === undefined ? { id: 'ds-1' } : opts.dataset
  const episode = opts.episode === undefined ? { meta: { index: 3, length: 250 } } : opts.episode
  const setCurrentFrame = opts.setCurrentFrame ?? vi.fn()
  const setFilters = opts.setFilters ?? vi.fn()
  const runDetection = opts.runDetection ?? vi.fn()

  mockedUseDatasetStore.mockImplementation(((selector: unknown) =>
    typeof selector === 'function'
      ? (selector as (s: unknown) => unknown)({ currentDataset: dataset })
      : dataset) as unknown as typeof useDatasetStore)
  mockedUseEpisodeStore.mockImplementation(((selector: unknown) =>
    typeof selector === 'function'
      ? (selector as (s: unknown) => unknown)({ currentEpisode: episode })
      : episode) as unknown as typeof useEpisodeStore)
  mockedUsePlaybackControls.mockReturnValue({
    currentFrame: opts.currentFrame ?? 0,
    setCurrentFrame,
    isPlaying: false,
    playbackSpeed: 1,
    togglePlayback: vi.fn(),
    setPlaybackSpeed: vi.fn(),
  } as unknown as ReturnType<typeof usePlaybackControls>)
  mockedUseObjectDetection.mockReturnValue({
    data: opts.data ?? null,
    filteredData: opts.filteredData ?? null,
    isLoading: opts.isLoading ?? false,
    isRunning: opts.isRunning ?? false,
    error: opts.error ?? null,
    needsRerun: opts.needsRerun ?? false,
    filters: opts.filters ?? { classes: [], minConfidence: 0.25 },
    setFilters,
    runDetection,
    clearCache: vi.fn(),
    availableClasses: opts.availableClasses ?? ['person', 'car'],
  } as unknown as ReturnType<typeof useObjectDetection>)

  return { setCurrentFrame, setFilters, runDetection }
}

beforeEach(() => {
  setup()
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('DetectionTab', () => {
  describe('null guards', () => {
    it('renders nothing when no dataset is loaded', () => {
      setup({ dataset: null })

      const { container } = render(<DetectionTab />)

      expect(container).toBeEmptyDOMElement()
    })

    it('renders nothing when no episode is loaded', () => {
      setup({ episode: null })

      const { container } = render(<DetectionTab />)

      expect(container).toBeEmptyDOMElement()
    })

    it('renders nothing when both dataset and episode are missing', () => {
      setup({ dataset: null, episode: null })

      const { container } = render(<DetectionTab />)

      expect(container).toBeEmptyDOMElement()
    })
  })

  describe('header and run button', () => {
    it('shows the YOLO11 title and the "Run Detection" button when no detection has run', () => {
      render(<DetectionTab />)

      expect(screen.getByText(/Object Detection \(YOLO11\)/i)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /run detection/i })).toBeEnabled()
      expect(screen.queryByText(/edits detected/i)).not.toBeInTheDocument()
    })

    it('shows the "Detecting..." spinner button when isRunning is true', () => {
      setup({ isRunning: true })

      render(<DetectionTab />)

      const button = screen.getByRole('button', { name: /detecting/i })
      expect(button).toBeDisabled()
    })

    it('disables the run button when isLoading is true even if not running', () => {
      setup({ isLoading: true })

      render(<DetectionTab />)

      expect(screen.getByRole('button', { name: /run detection/i })).toBeDisabled()
    })

    it('shows "Re-run Detection" and the edits-detected badge when needsRerun and data are present', () => {
      setup({ needsRerun: true, data: buildSummary(), filteredData: buildSummary() })

      render(<DetectionTab />)

      expect(screen.getByRole('button', { name: /re-run detection/i })).toBeEnabled()
      expect(screen.getByText(/edits detected/i)).toBeInTheDocument()
    })

    it('hides the edits-detected badge when needsRerun is true but no data is loaded yet', () => {
      setup({ needsRerun: true, data: null })

      render(<DetectionTab />)

      expect(screen.queryByText(/edits detected/i)).not.toBeInTheDocument()
    })

    it('invokes runDetection with the current minConfidence filter on click', () => {
      const runDetection = vi.fn()
      setup({
        runDetection,
        filters: { classes: ['person'], minConfidence: 0.7 },
      })

      render(<DetectionTab />)
      fireEvent.click(screen.getByRole('button', { name: /run detection/i }))

      expect(runDetection).toHaveBeenCalledTimes(1)
      expect(runDetection).toHaveBeenCalledWith({ confidence: 0.7 })
    })
  })

  describe('error display', () => {
    it('renders the error message when an Error instance is present', () => {
      setup({ error: new Error('boom') })

      render(<DetectionTab />)

      expect(screen.getByText(/Error:/)).toBeInTheDocument()
      expect(screen.getByText(/boom/)).toBeInTheDocument()
    })

    it('renders the fallback message when error is a non-Error value', () => {
      setup({ error: 'some string error' })

      render(<DetectionTab />)

      expect(screen.getByText(/Detection failed/)).toBeInTheDocument()
    })

    it('does not render any error block when error is null', () => {
      render(<DetectionTab />)

      expect(screen.queryByText(/Error:/)).not.toBeInTheDocument()
    })
  })

  describe('empty and loading states', () => {
    it('shows the empty state when no data and not running', () => {
      render(<DetectionTab />)

      expect(screen.getByText(/No detection results yet/i)).toBeInTheDocument()
      expect(screen.getByText(/Click "Run Detection"/i)).toBeInTheDocument()
      expect(screen.queryByText(/Processing frames/i)).not.toBeInTheDocument()
    })

    it('shows the processing state when running with no data', () => {
      setup({ isRunning: true, data: null })

      render(<DetectionTab />)

      expect(screen.getByText(/Processing frames/i)).toBeInTheDocument()
      expect(screen.queryByText(/No detection results yet/i)).not.toBeInTheDocument()
    })

    it('hides both empty and processing states once data is present', () => {
      setup({ data: buildSummary(), filteredData: buildSummary() })

      render(<DetectionTab />)

      expect(screen.queryByText(/No detection results yet/i)).not.toBeInTheDocument()
      expect(screen.queryByText(/Processing frames/i)).not.toBeInTheDocument()
    })
  })

  describe('results tabs', () => {
    it('renders the viewer tab with the current frame, image URL, and detection count', () => {
      const filteredData = buildSummary({
        detections_by_frame: [
          {
            frame: 5,
            detections: [buildDetection(), buildDetection({ class_name: 'car' })],
            processing_time_ms: 12,
          },
        ],
      })
      setup({ data: filteredData, filteredData, currentFrame: 5 })

      render(<DetectionTab />)

      const viewer = screen.getByTestId('detection-viewer')
      expect(viewer).toHaveAttribute(
        'data-image-url',
        '/api/datasets/ds-1/episodes/3/frames/5?camera=il-camera',
      )
      expect(viewer).toHaveAttribute('data-detection-count', '2')
      expect(screen.getByText(/Frame 5 - 2 detections/)).toBeInTheDocument()
    })

    it('uses the singular "detection" label when exactly one detection is present', () => {
      const filteredData = buildSummary({
        detections_by_frame: [{ frame: 0, detections: [buildDetection()], processing_time_ms: 5 }],
      })
      setup({ data: filteredData, filteredData })

      render(<DetectionTab />)

      expect(screen.getByText(/Frame 0 - 1 detection$/)).toBeInTheDocument()
    })

    it('falls back to zero detections when the current frame has no entry in filteredData', () => {
      const filteredData = buildSummary({
        detections_by_frame: [{ frame: 99, detections: [buildDetection()], processing_time_ms: 5 }],
      })
      setup({ data: filteredData, filteredData, currentFrame: 0 })

      render(<DetectionTab />)

      expect(screen.getByText(/Frame 0 - 0 detections/)).toBeInTheDocument()
      expect(screen.getByTestId('detection-viewer')).toHaveAttribute('data-detection-count', '0')
    })

    it('passes filteredData and totalFrames to the timeline', () => {
      const filteredData = buildSummary({
        detections_by_frame: [
          { frame: 0, detections: [], processing_time_ms: 5 },
          { frame: 1, detections: [buildDetection()], processing_time_ms: 5 },
        ],
      })
      setup({ data: filteredData, filteredData, currentFrame: 1 })

      render(<DetectionTab />)

      const timelines = screen.getAllByTestId('detection-timeline')
      expect(timelines.length).toBeGreaterThanOrEqual(1)
      expect(timelines[0]).toHaveAttribute('data-frame-count', '2')
      expect(timelines[0]).toHaveAttribute('data-total-frames', '250')
      expect(timelines[0]).toHaveAttribute('data-current-frame', '1')
    })

    it('falls back to totalFrames=100 when the episode meta length is missing', () => {
      const filteredData = buildSummary()
      setup({
        data: filteredData,
        filteredData,
        episode: { meta: { index: 0, length: 0 } },
      })

      render(<DetectionTab />)

      expect(screen.getAllByTestId('detection-timeline')[0]).toHaveAttribute(
        'data-total-frames',
        '100',
      )
    })

    it('passes filters and available classes to the filters tab', async () => {
      const user = userEvent.setup()
      const filteredData = buildSummary()
      setup({
        data: filteredData,
        filteredData,
        filters: { classes: ['car'], minConfidence: 0.42 },
        availableClasses: ['person', 'car', 'dog'],
      })

      render(<DetectionTab />)
      await user.click(screen.getByRole('tab', { name: /filter/i }))

      const filters = await screen.findByTestId('detection-filters')
      expect(filters).toHaveAttribute('data-min-confidence', '0.42')
      expect(filters).toHaveAttribute('data-classes', 'person,car,dog')
    })

    it('renders the charts component when filteredData is present', async () => {
      const user = userEvent.setup()
      const filteredData = buildSummary({ total_detections: 17 })
      setup({ data: buildSummary(), filteredData })

      render(<DetectionTab />)
      await user.click(screen.getByRole('tab', { name: /charts/i }))

      expect(await screen.findByTestId('detection-charts')).toHaveAttribute('data-total', '17')
    })

    it('omits the charts component when filteredData is null even though data is present', async () => {
      const user = userEvent.setup()
      setup({ data: buildSummary(), filteredData: null })

      render(<DetectionTab />)
      await user.click(screen.getByRole('tab', { name: /charts/i }))

      expect(screen.queryByTestId('detection-charts')).not.toBeInTheDocument()
    })

    it('passes an empty detectionsPerFrame array to the timeline when filteredData is null', () => {
      setup({ data: buildSummary(), filteredData: null })

      render(<DetectionTab />)

      const timelines = screen.getAllByTestId('detection-timeline')
      expect(timelines[0]).toHaveAttribute('data-frame-count', '0')
    })
  })
})
