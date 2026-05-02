import { waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useAISuggestion,
  useAnomalyDetection,
  useRequestAISuggestion,
  useTrajectoryAnalysis,
} from '@/hooks/use-ai-analysis'
import { renderHookWithQuery } from '@/test/render-hook-with-query'

vi.mock('@/api/ai-analysis', () => ({
  analyzeTrajectory: vi.fn(),
  detectAnomalies: vi.fn(),
  getAnnotationSuggestion: vi.fn(),
}))

import {
  analyzeTrajectory,
  detectAnomalies,
  getAnnotationSuggestion,
} from '@/api/ai-analysis'

const mockedAnalyze = vi.mocked(analyzeTrajectory)
const mockedDetect = vi.mocked(detectAnomalies)
const mockedSuggest = vi.mocked(getAnnotationSuggestion)

beforeEach(() => {
  mockedAnalyze.mockReset()
  mockedDetect.mockReset()
  mockedSuggest.mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

const validTrajectory = {
  positions: [
    [0, 0, 0],
    [1, 1, 1],
    [2, 2, 2],
  ],
  timestamps: [0, 1, 2],
}

describe('useAISuggestion', () => {
  it('is disabled without trajectory data', () => {
    const { result } = renderHookWithQuery(() =>
      useAISuggestion({ datasetId: 'd', episodeId: 'e' }),
    )
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockedSuggest).not.toHaveBeenCalled()
  })

  it('is disabled when positions has fewer than 3 entries', () => {
    const { result } = renderHookWithQuery(() =>
      useAISuggestion({
        datasetId: 'd',
        episodeId: 'e',
        trajectoryData: { positions: [[0]], timestamps: [0] },
      }),
    )
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockedSuggest).not.toHaveBeenCalled()
  })

  it('is disabled when explicitly disabled', () => {
    const { result } = renderHookWithQuery(() =>
      useAISuggestion({
        datasetId: 'd',
        episodeId: 'e',
        trajectoryData: validTrajectory,
        enabled: false,
      }),
    )
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockedSuggest).not.toHaveBeenCalled()
  })

  it('fetches suggestions when trajectory data is provided', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedSuggest.mockResolvedValueOnce({ confidence: 0.9 } as any)

    const { result } = renderHookWithQuery(() =>
      useAISuggestion({
        datasetId: 'd',
        episodeId: 'e',
        trajectoryData: validTrajectory,
      }),
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockedSuggest).toHaveBeenCalledWith(validTrajectory)
  })
})

describe('useTrajectoryAnalysis', () => {
  it('is disabled without trajectory data', () => {
    const { result } = renderHookWithQuery(() =>
      useTrajectoryAnalysis({ datasetId: 'd', episodeId: 'e' }),
    )
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockedAnalyze).not.toHaveBeenCalled()
  })

  it('fetches metrics when enabled with valid data', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedAnalyze.mockResolvedValueOnce({ smoothness: 0.8 } as any)

    const { result } = renderHookWithQuery(() =>
      useTrajectoryAnalysis({
        datasetId: 'd',
        episodeId: 'e',
        trajectoryData: validTrajectory,
      }),
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockedAnalyze).toHaveBeenCalledWith(validTrajectory)
  })
})

describe('useAnomalyDetection', () => {
  it('is disabled without trajectory data', () => {
    const { result } = renderHookWithQuery(() =>
      useAnomalyDetection({ datasetId: 'd', episodeId: 'e' }),
    )
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockedDetect).not.toHaveBeenCalled()
  })

  it('fetches anomalies when enabled with valid data', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedDetect.mockResolvedValueOnce({ anomalies: [], total_count: 0 } as any)

    const { result } = renderHookWithQuery(() =>
      useAnomalyDetection({
        datasetId: 'd',
        episodeId: 'e',
        trajectoryData: validTrajectory,
      }),
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockedDetect).toHaveBeenCalledWith(validTrajectory)
  })
})

describe('useRequestAISuggestion', () => {
  it('invokes the suggestion API on mutate', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedSuggest.mockResolvedValueOnce({ confidence: 0.5 } as any)

    const { result } = renderHookWithQuery(() => useRequestAISuggestion())

    result.current.mutate(validTrajectory)

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockedSuggest).toHaveBeenCalledWith(validTrajectory, expect.anything())
  })
})
