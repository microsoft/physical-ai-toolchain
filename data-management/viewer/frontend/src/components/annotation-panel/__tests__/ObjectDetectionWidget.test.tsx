import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ObjectDetectionWidget } from '@/components/annotation-panel/ObjectDetectionWidget'
import { useAnnotationStore } from '@/stores/annotation-store'
import { useDatasetStore } from '@/stores/dataset-store'
import { useEpisodeStore } from '@/stores/episode-store'
import type { DatasetInfo, EpisodeAnnotation, EpisodeData } from '@/types'

const runDetectionMock = vi.fn()
vi.mock('@/api/detection', () => ({
  runDetection: (...args: unknown[]) => runDetectionMock(...args),
}))

const baseAnnotation: EpisodeAnnotation = {
  annotatorId: 'tester',
  timestamp: '2026-01-01T00:00:00.000Z',
  taskCompleteness: { rating: 'unknown', confidence: 3 },
  trajectoryQuality: {
    overallScore: 3,
    metrics: { smoothness: 3, efficiency: 3, safety: 3, precision: 3 },
    flags: [],
  },
  dataQuality: { overallQuality: 'good', issues: [] },
  anomalies: { anomalies: [] },
}

const dataset: DatasetInfo = {
  id: 'ds',
  name: 'Dataset',
  totalEpisodes: 1,
  fps: 30,
  features: {},
  tasks: [],
}

const episode: EpisodeData = {
  meta: { index: 0, length: 5, taskIndex: 0, hasAnnotations: false },
  videoUrls: {},
  cameras: ['front'],
  trajectoryData: [],
}

function seed({ withStores = true }: { withStores?: boolean } = {}) {
  useAnnotationStore.getState().clear()
  useDatasetStore.getState().reset()
  useEpisodeStore.getState().reset()
  if (withStores) {
    useDatasetStore.setState({ currentDataset: dataset })
    useEpisodeStore.getState().setCurrentEpisode(episode)
    useAnnotationStore.getState().loadAnnotation(baseAnnotation)
  }
}

const successSummary = {
  detections_by_frame: [
    {
      frame: 0,
      detections: [{ class_id: 0, class_name: 'block', confidence: 0.91, bbox: [0, 0, 10, 10] }],
    },
  ],
}

describe('ObjectDetectionWidget', () => {
  beforeEach(() => {
    runDetectionMock.mockReset()
    seed()
  })

  afterEach(() => {
    useAnnotationStore.getState().clear()
    useDatasetStore.getState().reset()
    useEpisodeStore.getState().reset()
  })

  it('shows the empty state without a dataset or episode', () => {
    seed({ withStores: false })
    render(<ObjectDetectionWidget />)
    expect(screen.getByText(/no episode selected/i)).toBeInTheDocument()
  })

  it('renders the detection controls when an episode is selected', () => {
    render(<ObjectDetectionWidget />)
    expect(screen.getByRole('button', { name: /detect/i })).toBeInTheDocument()
    expect(screen.getByText(/reference frame/i)).toBeInTheDocument()
  })

  it('adds and removes detection labels', async () => {
    const user = userEvent.setup()
    render(<ObjectDetectionWidget />)
    const input = screen.getByRole('textbox')

    await user.type(input, 'red block{Enter}')
    expect(screen.getByText('red block')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /remove label red block/i }))
    expect(screen.queryByText('red block')).not.toBeInTheDocument()
  })

  it('runs detection and persists the results on save', async () => {
    const user = userEvent.setup()
    runDetectionMock.mockResolvedValue(successSummary)
    render(<ObjectDetectionWidget />)

    await user.click(screen.getByRole('button', { name: /detect/i }))

    expect(await screen.findByText(/detections \(1\)/i)).toBeInTheDocument()
    expect(screen.getByText('block')).toBeInTheDocument()
    expect(runDetectionMock).toHaveBeenCalledWith('ds', 0, expect.objectContaining({ frames: [0] }))

    await user.click(screen.getByRole('button', { name: /^save$/i }))
    expect(useAnnotationStore.getState().currentAnnotation?.objectDetections).toHaveLength(1)
  })

  it('surfaces an error when detection fails', async () => {
    const user = userEvent.setup()
    runDetectionMock.mockRejectedValue(new Error('detector offline'))
    render(<ObjectDetectionWidget />)

    await user.click(screen.getByRole('button', { name: /detect/i }))

    expect(await screen.findByText(/detector offline/i)).toBeInTheDocument()
  })

  it('restores previously saved detections for the frame', async () => {
    useAnnotationStore.getState().clear()
    useAnnotationStore.getState().loadAnnotation({
      ...baseAnnotation,
      objectDetections: [
        {
          frameIndex: 0,
          camera: 'front',
          queriedLabels: ['block'],
          detections: [{ label: 'block', confidence: 0.88, bbox: [0, 0, 5, 5] }],
          model: 'yolov8s-world',
        },
      ],
    })
    render(<ObjectDetectionWidget />)

    expect(await screen.findByText(/detections \(1\)/i)).toBeInTheDocument()
    expect(screen.getByText(/1 saved/i)).toBeInTheDocument()
  })
})
