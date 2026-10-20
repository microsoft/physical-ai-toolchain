import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
  detectionsByFrame: [
    {
      frame: 0,
      detections: [{ classId: 0, className: 'block', confidence: 0.91, bbox: [0, 0, 10, 10] }],
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
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('accepts comma, tab and blur labels, deduplicates them and removes the last label with Backspace', async () => {
    const user = userEvent.setup()
    render(<ObjectDetectionWidget />)
    const input = screen.getByRole('textbox')
    await user.type(input, 'cube,')
    await user.type(input, 'cube{Enter}')
    expect(screen.getAllByRole('button', { name: 'Remove label cube' })).toHaveLength(1)
    await user.type(input, 'gripper')
    await user.tab()
    expect(screen.getByRole('button', { name: 'Remove label gripper' })).toBeVisible()
    await user.click(input)
    await user.keyboard('{Backspace}')
    expect(screen.queryByRole('button', { name: 'Remove label gripper' })).not.toBeInTheDocument()
    await user.type(input, 'table')
    fireEvent.blur(input)
    expect(screen.getByRole('button', { name: 'Remove label table' })).toBeVisible()
  })

  it('runs a labelled reference frame, saves its model and clears only the selected saved frame', async () => {
    const user = userEvent.setup()
    runDetectionMock.mockResolvedValue({
      detectionsByFrame: [{ ...successSummary.detectionsByFrame[0], frame: 2 }],
    })
    vi.stubGlobal(
      'confirm',
      vi.fn(() => true),
    )
    render(<ObjectDetectionWidget />)
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Reference frame' }), {
      target: { value: '2' },
    })
    await user.type(screen.getByRole('textbox'), 'cube{Enter}')
    await user.click(screen.getByRole('button', { name: 'Detect' }))
    expect(await screen.findByRole('status')).toHaveTextContent('frame 2')
    expect(runDetectionMock).toHaveBeenCalledWith('ds', 0, {
      frames: [2],
      labels: ['cube'],
      model: 'yolov8s-world',
      camera: 'front',
    })
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(useAnnotationStore.getState().currentAnnotation?.objectDetections?.[0]).toMatchObject({
      frameIndex: 2,
      queriedLabels: ['cube'],
      model: 'yolov8s-world',
    })
    await user.click(screen.getByRole('button', { name: 'Remove saved' }))
    expect(useAnnotationStore.getState().currentAnnotation?.objectDetections).toEqual([])
    await user.click(screen.getByTitle('Reset to first frame'))
    expect(screen.getByRole('spinbutton', { name: 'Reference frame' })).toHaveValue(0)
  })

  it('reports empty and pending detections and clears a previous result without saving', async () => {
    const user = userEvent.setup()
    let finish!: (value: unknown) => void
    runDetectionMock.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      }),
    )
    render(<ObjectDetectionWidget />)
    await user.type(screen.getByRole('textbox'), 'cube{Enter}')
    await user.click(screen.getByRole('button', { name: 'Detect' }))
    expect(screen.getByRole('button', { name: /Detecting/ })).toBeDisabled()
    await act(async () => finish({ detectionsByFrame: [] }))
    expect(screen.getByText(/No matches for the supplied labels/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Clear' }))
    expect(screen.queryByText(/No matches/)).not.toBeInTheDocument()
    expect(useAnnotationStore.getState().currentAnnotation?.objectDetections).toBeUndefined()
  })

  it('draws loaded detections at preview scale and ignores image callbacks after unmount', async () => {
    const user = userEvent.setup()
    const images: Array<{
      width: number
      height: number
      onload: (() => void) | null
      onerror: (() => void) | null
    }> = []
    class PreviewImage {
      width = 100
      height = 50
      onload: (() => void) | null = null
      onerror: (() => void) | null = null
      constructor() {
        images.push(this)
      }
    }
    vi.stubGlobal('Image', PreviewImage)
    const context = {
      drawImage: vi.fn(),
      strokeRect: vi.fn(),
      measureText: vi.fn(() => ({ width: 40 })),
      fillRect: vi.fn(),
      fillText: vi.fn(),
      clearRect: vi.fn(),
    }
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
      context as unknown as CanvasRenderingContext2D,
    )
    runDetectionMock.mockResolvedValue(successSummary)
    const { unmount } = render(<ObjectDetectionWidget />)
    await user.click(screen.getByRole('button', { name: 'Detect' }))
    await screen.findByRole('status')
    const loaded = images.at(-1)!
    act(() => loaded.onload?.())
    expect(screen.getByRole('status')).toHaveTextContent('100×50px')
    expect(context.strokeRect).toHaveBeenCalledWith(0, 0, 32, 32)
    act(() => images.at(-1)!.onerror?.())
    expect(context.clearRect).toHaveBeenCalled()
    unmount()
    context.drawImage.mockClear()
    loaded.onload?.()
    expect(context.drawImage).not.toHaveBeenCalled()
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

  it('keeps camera selection controlled when cameras arrive asynchronously', async () => {
    const consoleWarn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    useEpisodeStore.getState().setCurrentEpisode({ ...episode, cameras: [] })
    render(<ObjectDetectionWidget />)

    act(() => {
      useEpisodeStore.getState().setCurrentEpisode(episode)
    })

    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: /camera/i })).toBeInTheDocument(),
    )
    await waitFor(() => {
      expect(
        consoleWarn.mock.calls.some(([message]) =>
          String(message).includes('uncontrolled to controlled'),
        ),
      ).toBe(false)
    })
    consoleWarn.mockRestore()
  })

  it('adds and removes detection labels', async () => {
    const user = userEvent.setup()
    render(<ObjectDetectionWidget />)
    const input = screen.getByRole('textbox')
    expect(input).toHaveClass('focus-visible:ring-2')
    expect(input).not.toHaveClass('focus-visible:ring-0')

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

    expect(await screen.findByRole('status')).toHaveTextContent(/detections \(1\)/i)
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

    expect(await screen.findByRole('alert')).toHaveTextContent(/detector offline/i)
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

    expect(await screen.findByRole('status')).toHaveTextContent(/detections \(1\)/i)
    expect(screen.getByText(/1 saved/i)).toBeInTheDocument()
  })
})
