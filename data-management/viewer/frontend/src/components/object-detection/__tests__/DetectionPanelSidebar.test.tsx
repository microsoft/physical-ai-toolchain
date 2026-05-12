import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Detection, DetectionFilters, EpisodeDetectionSummary } from '@/types/detection'

import { DetectionPanelSidebar } from '../DetectionPanelSidebar'

vi.mock('../DetectionFilters', () => ({
  DetectionFilters: ({
    availableClasses,
    filters,
    onFiltersChange,
  }: {
    availableClasses: string[]
    filters: DetectionFilters
    onFiltersChange: (filters: DetectionFilters) => void
  }) => {
    const handleChange = () => onFiltersChange({ ...filters, minConfidence: 0.9 })
    return (
      <div
        data-testid="filters-panel"
        data-classes={availableClasses.join(',')}
        data-min-confidence={filters.minConfidence}
        data-selected-classes={filters.classes.join(',')}
        onClick={handleChange}
        onKeyDown={handleChange}
        role="button"
        tabIndex={0}
      />
    )
  },
}))

const buildDetection = (overrides: Partial<Detection> = {}): Detection => ({
  class_id: 0,
  class_name: 'person',
  confidence: 0.95,
  bbox: [10, 20, 30, 40],
  ...overrides,
})

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

const defaultFilters: DetectionFilters = {
  classes: [],
  minConfidence: 0.25,
}

const renderSidebar = (props: Partial<React.ComponentProps<typeof DetectionPanelSidebar>> = {}) => {
  const onFiltersChange = vi.fn()
  const utils = render(
    <DetectionPanelSidebar
      availableClasses={props.availableClasses ?? ['person', 'car']}
      currentDetections={props.currentDetections ?? []}
      currentFrame={props.currentFrame ?? 0}
      data={props.data ?? null}
      filteredData={props.filteredData ?? null}
      filters={props.filters ?? defaultFilters}
      onFiltersChange={props.onFiltersChange ?? onFiltersChange}
    />,
  )
  return { ...utils, onFiltersChange }
}

describe('DetectionPanelSidebar', () => {
  describe('Detection Filters card', () => {
    it('always renders the filters card with the filters panel', () => {
      renderSidebar()

      expect(screen.getByText('Detection Filters')).toBeInTheDocument()
      expect(screen.getByTestId('filters-panel')).toBeInTheDocument()
    })

    it('forwards availableClasses, filters, and onFiltersChange to the filters panel', () => {
      const onFiltersChange = vi.fn()
      const filters: DetectionFilters = { classes: ['person'], minConfidence: 0.5 }
      renderSidebar({
        availableClasses: ['person', 'car', 'dog'],
        filters,
        onFiltersChange,
      })

      const panel = screen.getByTestId('filters-panel')
      expect(panel).toHaveAttribute('data-classes', 'person,car,dog')
      expect(panel).toHaveAttribute('data-min-confidence', '0.5')
      expect(panel).toHaveAttribute('data-selected-classes', 'person')

      panel.click()
      expect(onFiltersChange).toHaveBeenCalledWith({ classes: ['person'], minConfidence: 0.9 })
    })
  })

  describe('Frame Detections card', () => {
    it('does not render when data is null', () => {
      renderSidebar({
        data: null,
        currentDetections: [buildDetection()],
      })

      expect(screen.queryByText(/Detections$/)).not.toBeInTheDocument()
    })

    it('does not render when data is undefined', () => {
      renderSidebar({
        data: undefined,
        currentDetections: [buildDetection()],
      })

      expect(screen.queryByText(/Detections$/)).not.toBeInTheDocument()
    })

    it('does not render when currentDetections is empty', () => {
      renderSidebar({
        data: buildSummary(),
        currentDetections: [],
      })

      expect(screen.queryByText(/Detections$/)).not.toBeInTheDocument()
    })

    it('renders the Frame Detections card with the current frame number when data and detections exist', () => {
      renderSidebar({
        data: buildSummary(),
        currentDetections: [buildDetection()],
        currentFrame: 42,
      })

      expect(screen.getByText('Frame 42 Detections')).toBeInTheDocument()
    })

    it('renders one row per detection with class name and confidence percentage', () => {
      renderSidebar({
        data: buildSummary(),
        currentDetections: [
          buildDetection({ class_name: 'person', confidence: 0.953, bbox: [0, 0, 1, 1] }),
          buildDetection({ class_name: 'car', confidence: 0.5, bbox: [2, 2, 3, 3] }),
        ],
      })

      expect(screen.getByText('person')).toBeInTheDocument()
      expect(screen.getByText('95.3%')).toBeInTheDocument()
      expect(screen.getByText('car')).toBeInTheDocument()
      expect(screen.getByText('50.0%')).toBeInTheDocument()
    })
  })

  describe('Summary card', () => {
    it('does not render when data is null', () => {
      renderSidebar({ data: null })

      expect(screen.queryByText('Summary')).not.toBeInTheDocument()
    })

    it('does not render when data is undefined', () => {
      renderSidebar({ data: undefined })

      expect(screen.queryByText('Summary')).not.toBeInTheDocument()
    })

    it('renders summary metrics from filteredData, availableClasses, data, and filters', () => {
      renderSidebar({
        availableClasses: ['person', 'car', 'dog'],
        data: buildSummary({ processed_frames: 75 }),
        filteredData: buildSummary({ total_detections: 123 }),
        filters: { classes: [], minConfidence: 0.42 },
      })

      expect(screen.getByText('Summary')).toBeInTheDocument()
      expect(screen.getByText('123')).toBeInTheDocument()
      expect(screen.getByText('Total')).toBeInTheDocument()
      expect(screen.getByText('3')).toBeInTheDocument()
      expect(screen.getByText('Classes')).toBeInTheDocument()
      expect(screen.getByText('75')).toBeInTheDocument()
      expect(screen.getByText('Frames')).toBeInTheDocument()
      expect(screen.getByText('42%')).toBeInTheDocument()
      expect(screen.getByText('Min Conf')).toBeInTheDocument()
    })

    it('falls back to 0 for total when filteredData is null', () => {
      renderSidebar({
        availableClasses: [],
        data: buildSummary({ processed_frames: 10 }),
        filteredData: null,
        filters: { classes: [], minConfidence: 0 },
      })

      const totalLabel = screen.getByText('Total')
      const totalCard = totalLabel.parentElement
      expect(totalCard).not.toBeNull()
      expect(totalCard?.textContent).toContain('0')
    })

    it('rounds Min Conf percentage using toFixed(0)', () => {
      renderSidebar({
        data: buildSummary(),
        filters: { classes: [], minConfidence: 0.756 },
      })

      expect(screen.getByText('76%')).toBeInTheDocument()
    })
  })
})
