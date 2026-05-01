import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getAnnotationSuggestionMock = vi.fn()
const analyzeTrajectoryMock = vi.fn()
const detectAnomaliesMock = vi.fn()

vi.mock('@/api/ai-analysis', () => ({
  getAnnotationSuggestion: (...args: unknown[]) => getAnnotationSuggestionMock(...args),
  analyzeTrajectory: (...args: unknown[]) => analyzeTrajectoryMock(...args),
  detectAnomalies: (...args: unknown[]) => detectAnomaliesMock(...args),
}))

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
}

function makeWrapper(client: QueryClient) {
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children)
}

const positions3 = [
  [0, 0, 0],
  [1, 1, 1],
  [2, 2, 2],
]

beforeEach(() => {
  getAnnotationSuggestionMock.mockReset()
  analyzeTrajectoryMock.mockReset()
  detectAnomaliesMock.mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useAISuggestion', () => {
  it('fetches suggestion when trajectory has at least 3 positions', async () => {
    getAnnotationSuggestionMock.mockResolvedValueOnce({ rating: 'success' })

    const { useAISuggestion } = await import('@/hooks/use-ai-analysis')
    const wrapper = makeWrapper(makeQueryClient())
    const { result } = renderHook(
      () =>
        useAISuggestion({
          datasetId: 'ds-1',
          episodeId: 'ep-0',
          trajectoryData: { positions: positions3 } as never,
        }),
      { wrapper },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(getAnnotationSuggestionMock).toHaveBeenCalledTimes(1)
    expect(result.current.data).toEqual({ rating: 'success' })
  })

  it('is disabled when trajectoryData is undefined', async () => {
    const { useAISuggestion } = await import('@/hooks/use-ai-analysis')
    const wrapper = makeWrapper(makeQueryClient())
    renderHook(
      () =>
        useAISuggestion({
          datasetId: 'ds-1',
          episodeId: 'ep-0',
          trajectoryData: undefined,
        }),
      { wrapper },
    )

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(getAnnotationSuggestionMock).not.toHaveBeenCalled()
  })

  it('is disabled when fewer than 3 positions are provided', async () => {
    const { useAISuggestion } = await import('@/hooks/use-ai-analysis')
    const wrapper = makeWrapper(makeQueryClient())
    renderHook(
      () =>
        useAISuggestion({
          datasetId: 'ds-1',
          episodeId: 'ep-0',
          trajectoryData: {
            positions: [
              [0, 0, 0],
              [1, 1, 1],
            ],
          } as never,
        }),
      { wrapper },
    )

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(getAnnotationSuggestionMock).not.toHaveBeenCalled()
  })

  it('is disabled when enabled is false', async () => {
    const { useAISuggestion } = await import('@/hooks/use-ai-analysis')
    const wrapper = makeWrapper(makeQueryClient())
    renderHook(
      () =>
        useAISuggestion({
          datasetId: 'ds-1',
          episodeId: 'ep-0',
          trajectoryData: { positions: positions3 } as never,
          enabled: false,
        }),
      { wrapper },
    )

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(getAnnotationSuggestionMock).not.toHaveBeenCalled()
  })
})

describe('useTrajectoryAnalysis', () => {
  it('fetches metrics for valid trajectory', async () => {
    analyzeTrajectoryMock.mockResolvedValueOnce({ smoothness: 0.9 })

    const { useTrajectoryAnalysis } = await import('@/hooks/use-ai-analysis')
    const wrapper = makeWrapper(makeQueryClient())
    const { result } = renderHook(
      () =>
        useTrajectoryAnalysis({
          datasetId: 'ds-1',
          episodeId: 'ep-0',
          trajectoryData: { positions: positions3 } as never,
        }),
      { wrapper },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(analyzeTrajectoryMock).toHaveBeenCalledTimes(1)
    expect(result.current.data).toEqual({ smoothness: 0.9 })
  })

  it('is disabled without trajectoryData', async () => {
    const { useTrajectoryAnalysis } = await import('@/hooks/use-ai-analysis')
    const wrapper = makeWrapper(makeQueryClient())
    renderHook(
      () =>
        useTrajectoryAnalysis({
          datasetId: 'ds-1',
          episodeId: 'ep-0',
          trajectoryData: undefined,
        }),
      { wrapper },
    )

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(analyzeTrajectoryMock).not.toHaveBeenCalled()
  })
})

describe('useAnomalyDetection', () => {
  it('fetches anomalies for valid trajectory', async () => {
    detectAnomaliesMock.mockResolvedValueOnce({ anomalies: [] })

    const { useAnomalyDetection } = await import('@/hooks/use-ai-analysis')
    const wrapper = makeWrapper(makeQueryClient())
    const { result } = renderHook(
      () =>
        useAnomalyDetection({
          datasetId: 'ds-1',
          episodeId: 'ep-0',
          trajectoryData: { positions: positions3 } as never,
        }),
      { wrapper },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(detectAnomaliesMock).toHaveBeenCalledTimes(1)
    expect(result.current.data).toEqual({ anomalies: [] })
  })

  it('is disabled with fewer than 3 positions', async () => {
    const { useAnomalyDetection } = await import('@/hooks/use-ai-analysis')
    const wrapper = makeWrapper(makeQueryClient())
    renderHook(
      () =>
        useAnomalyDetection({
          datasetId: 'ds-1',
          episodeId: 'ep-0',
          trajectoryData: { positions: [[0, 0, 0]] } as never,
        }),
      { wrapper },
    )

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(detectAnomaliesMock).not.toHaveBeenCalled()
  })
})

describe('useRequestAISuggestion', () => {
  it('invokes getAnnotationSuggestion on mutate', async () => {
    getAnnotationSuggestionMock.mockResolvedValueOnce({ rating: 'success' })

    const { useRequestAISuggestion } = await import('@/hooks/use-ai-analysis')
    const wrapper = makeWrapper(makeQueryClient())
    const { result } = renderHook(() => useRequestAISuggestion(), { wrapper })

    const payload = { positions: positions3 } as never
    act(() => {
      result.current.mutate(payload)
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(getAnnotationSuggestionMock).toHaveBeenCalledWith(payload, expect.anything())
    expect(result.current.data).toEqual({ rating: 'success' })
  })
})
