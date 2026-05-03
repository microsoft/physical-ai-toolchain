import { act, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useAISuggestion,
  useAnomalyDetection,
  useRequestAISuggestion,
  useTrajectoryAnalysis,
} from '@/hooks/use-ai-analysis'
import { renderHookWithProviders } from '@/test-utils/render-hook'

const getAnnotationSuggestionMock = vi.fn()
const analyzeTrajectoryMock = vi.fn()
const detectAnomaliesMock = vi.fn()

vi.mock('@/api/ai-analysis', () => ({
  getAnnotationSuggestion: (...args: unknown[]) => getAnnotationSuggestionMock(...args),
  analyzeTrajectory: (...args: unknown[]) => analyzeTrajectoryMock(...args),
  detectAnomalies: (...args: unknown[]) => detectAnomaliesMock(...args),
}))

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

    const { result } = renderHookWithProviders(() =>
      useAISuggestion({
        datasetId: 'ds-1',
        episodeId: 'ep-0',
        trajectoryData: { positions: positions3 } as never,
      }),
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(getAnnotationSuggestionMock).toHaveBeenCalledTimes(1)
    expect(result.current.data).toEqual({ rating: 'success' })
  })

  it('is disabled when trajectoryData is undefined', async () => {
    renderHookWithProviders(() =>
      useAISuggestion({
        datasetId: 'ds-1',
        episodeId: 'ep-0',
        trajectoryData: undefined,
      }),
    )

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(getAnnotationSuggestionMock).not.toHaveBeenCalled()
  })

  it('is disabled when fewer than 3 positions are provided', async () => {
    renderHookWithProviders(() =>
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
    )

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(getAnnotationSuggestionMock).not.toHaveBeenCalled()
  })

  it('is disabled when enabled is false', async () => {
    renderHookWithProviders(() =>
      useAISuggestion({
        datasetId: 'ds-1',
        episodeId: 'ep-0',
        trajectoryData: { positions: positions3 } as never,
        enabled: false,
      }),
    )

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(getAnnotationSuggestionMock).not.toHaveBeenCalled()
  })
})

describe('useTrajectoryAnalysis', () => {
  it('fetches metrics for valid trajectory', async () => {
    analyzeTrajectoryMock.mockResolvedValueOnce({ smoothness: 0.9 })

    const { result } = renderHookWithProviders(() =>
      useTrajectoryAnalysis({
        datasetId: 'ds-1',
        episodeId: 'ep-0',
        trajectoryData: { positions: positions3 } as never,
      }),
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(analyzeTrajectoryMock).toHaveBeenCalledTimes(1)
    expect(result.current.data).toEqual({ smoothness: 0.9 })
  })

  it('is disabled without trajectoryData', async () => {
    renderHookWithProviders(() =>
      useTrajectoryAnalysis({
        datasetId: 'ds-1',
        episodeId: 'ep-0',
        trajectoryData: undefined,
      }),
    )

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(analyzeTrajectoryMock).not.toHaveBeenCalled()
  })
})

describe('useAnomalyDetection', () => {
  it('fetches anomalies for valid trajectory', async () => {
    detectAnomaliesMock.mockResolvedValueOnce({ anomalies: [] })

    const { result } = renderHookWithProviders(() =>
      useAnomalyDetection({
        datasetId: 'ds-1',
        episodeId: 'ep-0',
        trajectoryData: { positions: positions3 } as never,
      }),
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(detectAnomaliesMock).toHaveBeenCalledTimes(1)
    expect(result.current.data).toEqual({ anomalies: [] })
  })

  it('is disabled with fewer than 3 positions', async () => {
    renderHookWithProviders(() =>
      useAnomalyDetection({
        datasetId: 'ds-1',
        episodeId: 'ep-0',
        trajectoryData: { positions: [[0, 0, 0]] } as never,
      }),
    )

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(detectAnomaliesMock).not.toHaveBeenCalled()
  })
})

describe('useRequestAISuggestion', () => {
  it('invokes getAnnotationSuggestion on mutate', async () => {
    getAnnotationSuggestionMock.mockResolvedValueOnce({ rating: 'success' })

    const { result } = renderHookWithProviders(() => useRequestAISuggestion())

    const payload = { positions: positions3 } as never
    act(() => {
      result.current.mutate(payload)
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(getAnnotationSuggestionMock).toHaveBeenCalledWith(payload, expect.anything())
    expect(result.current.data).toEqual({ rating: 'success' })
  })

  it('does not throw when the consumer unmounts before the request resolves', async () => {
    let resolveSuggestion!: (value: unknown) => void
    const deferred = new Promise<unknown>((resolve) => {
      resolveSuggestion = resolve
    })
    getAnnotationSuggestionMock.mockImplementationOnce(() => deferred)

    const { result, unmount } = renderHookWithProviders(() => useRequestAISuggestion())

    act(() => {
      result.current.mutate({ positions: positions3 } as never)
    })

    unmount()
    resolveSuggestion({ rating: 'success' })
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
})
