import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useObjectDetection } from '@/hooks/use-object-detection'
import { useDatasetStore, useEditStore, useEpisodeStore } from '@/stores'
import type {
  DetectionRequest,
  EpisodeDetectionSummary,
} from '@/types/detection'

vi.mock('@/api/detection', () => ({
  clearDetections: vi.fn(),
  getDetections: vi.fn(),
  runDetection: vi.fn(),
}))

import { clearDetections, getDetections, runDetection } from '@/api/detection'

const mockedGet = vi.mocked(getDetections)
const mockedRun = vi.mocked(runDetection)
const mockedClear = vi.mocked(clearDetections)

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children)
  return { Wrapper, queryClient }
}

function makeSummary(overrides: Partial<EpisodeDetectionSummary> = {}): EpisodeDetectionSummary {
  return {
    total_frames: 2,
    processed_frames: 2,
    total_detections: 3,
    detections_by_frame: [
      {
        frame: 0,
        processing_time_ms: 10,
        detections: [
          { class_id: 0, class_name: 'person', confidence: 0.9, bbox: [0, 0, 10, 10] },
          { class_id: 1, class_name: 'cup', confidence: 0.2, bbox: [10, 10, 20, 20] },
        ],
      },
      {
        frame: 1,
        processing_time_ms: 12,
        detections: [
          { class_id: 0, class_name: 'person', confidence: 0.6, bbox: [5, 5, 15, 15] },
        ],
      },
    ],
    class_summary: {
      person: { count: 2, avg_confidence: 0.75 },
      cup: { count: 1, avg_confidence: 0.2 },
    },
    ...overrides,
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const datasetInfo: any = { id: 'ds-1', name: 'ds', path: '/x', episode_count: 1 }
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const episodeInfo: any = {
  meta: { index: 0, length: 2, dataset_id: 'ds-1' },
  frames: [],
}

describe('useObjectDetection', () => {
  beforeEach(() => {
    mockedGet.mockReset()
    mockedRun.mockReset()
    mockedClear.mockReset()
    useDatasetStore.getState().reset()
    useEpisodeStore.getState().reset()
    useEditStore.getState().resetEdits()
    useDatasetStore.setState({ datasets: [datasetInfo], currentDataset: datasetInfo })
    useEpisodeStore.setState({ episodes: [episodeInfo], currentEpisode: episodeInfo })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetches cached detections when dataset and episode selected', async () => {
    mockedGet.mockResolvedValueOnce(makeSummary())
    const { Wrapper } = makeWrapper()

    const { result } = renderHook(() => useObjectDetection(), { wrapper: Wrapper })

    await waitFor(() => {
      expect(result.current.data).toBeDefined()
    })
    expect(mockedGet).toHaveBeenCalledWith('ds-1', 0)
    expect(result.current.availableClasses).toEqual(expect.arrayContaining(['person', 'cup']))
  })

  it('does not query when no dataset selected', async () => {
    useDatasetStore.setState({ currentDataset: null })
    const { Wrapper } = makeWrapper()

    const { result } = renderHook(() => useObjectDetection(), { wrapper: Wrapper })

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })
    expect(mockedGet).not.toHaveBeenCalled()
  })

  it('does not query when no episode selected', async () => {
    useEpisodeStore.setState({ currentEpisode: null })
    const { Wrapper } = makeWrapper()

    const { result } = renderHook(() => useObjectDetection(), { wrapper: Wrapper })

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })
    expect(mockedGet).not.toHaveBeenCalled()
  })

  it('filters detections by minimum confidence', async () => {
    mockedGet.mockResolvedValueOnce(makeSummary())
    const { Wrapper } = makeWrapper()

    const { result } = renderHook(() => useObjectDetection(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.data).toBeDefined())

    act(() => {
      result.current.setFilters({ classes: [], minConfidence: 0.5 })
    })

    expect(result.current.filteredData?.total_detections).toBe(2)
  })

  it('filters detections by class', async () => {
    mockedGet.mockResolvedValueOnce(makeSummary())
    const { Wrapper } = makeWrapper()

    const { result } = renderHook(() => useObjectDetection(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.data).toBeDefined())

    act(() => {
      result.current.setFilters({ classes: ['cup'], minConfidence: 0 })
    })

    expect(result.current.filteredData?.total_detections).toBe(1)
  })

  it('runDetection updates query cache and clears needsRerun', async () => {
    mockedGet.mockResolvedValueOnce(null)
    const newSummary = makeSummary({ total_detections: 5 })
    mockedRun.mockResolvedValueOnce(newSummary)
    const { Wrapper, queryClient } = makeWrapper()

    const { result } = renderHook(() => useObjectDetection(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    const request: DetectionRequest = { confidence: 0.3 }
    await act(async () => {
      result.current.runDetection(request)
    })

    await waitFor(() => {
      expect(result.current.data?.total_detections).toBe(5)
    })
    expect(mockedRun).toHaveBeenCalledWith('ds-1', 0, request)
    expect(result.current.needsRerun).toBe(false)
    const cached = queryClient.getQueryData(['detection', 'ds-1', 0])
    expect(cached).toEqual(newSummary)
  })

  it('clearCache invalidates the detection query', async () => {
    mockedGet.mockResolvedValueOnce(makeSummary())
    mockedClear.mockResolvedValueOnce({ cleared: true })
    const { Wrapper } = makeWrapper()

    const { result } = renderHook(() => useObjectDetection(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.data).toBeDefined())

    mockedGet.mockResolvedValueOnce(null)
    await act(async () => {
      result.current.clearCache()
    })

    await waitFor(() => {
      expect(mockedClear).toHaveBeenCalledWith('ds-1', 0)
    })
  })

  it('exposes runDetection mutation pending flag', async () => {
    mockedGet.mockResolvedValueOnce(null)
    let resolveRun: (value: EpisodeDetectionSummary) => void = () => {}
    mockedRun.mockReturnValueOnce(
      new Promise<EpisodeDetectionSummary>((resolve) => {
        resolveRun = resolve
      }),
    )
    const { Wrapper } = makeWrapper()

    const { result } = renderHook(() => useObjectDetection(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    act(() => {
      result.current.runDetection({})
    })

    await waitFor(() => expect(result.current.isRunning).toBe(true))

    await act(async () => {
      resolveRun(makeSummary())
    })

    await waitFor(() => expect(result.current.isRunning).toBe(false))
  })
})
