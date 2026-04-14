import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createQueryWrapper } from './test-utils'

// ============================================================================
// Mocks
// ============================================================================

const mockGetAnnotationSuggestion = vi.fn()
const mockAnalyzeTrajectory = vi.fn()
const mockDetectAnomalies = vi.fn()

vi.mock('@/api/ai-analysis', () => ({
  getAnnotationSuggestion: (...args: unknown[]) => mockGetAnnotationSuggestion(...args),
  analyzeTrajectory: (...args: unknown[]) => mockAnalyzeTrajectory(...args),
  detectAnomalies: (...args: unknown[]) => mockDetectAnomalies(...args),
}))

// ============================================================================
// Fixtures
// ============================================================================

const trajectoryPositions = [
  [0, 0, 0],
  [1, 1, 1],
  [2, 2, 2],
]

const shortPositions = [
  [0, 0, 0],
  [1, 1, 1],
]

const suggestionRequest = {
  dataset_id: 'ds-1',
  episode_id: 'ep-1',
  positions: trajectoryPositions,
  timestamps: [0, 1, 2],
}

const trajectoryData = {
  positions: trajectoryPositions,
  timestamps: [0, 1, 2],
}

const anomalyRequest = {
  positions: trajectoryPositions,
  timestamps: [0, 1, 2],
}

const suggestionResponse = {
  rating: 4,
  quality: 'good',
  issues: [],
  confidence: 0.9,
}

const trajectoryMetrics = {
  smoothness: 0.85,
  efficiency: 0.92,
  anomaly_score: 0.1,
}

const anomalyResponse = {
  anomalies: [{ index: 1, type: 'spike', score: 0.8 }],
  overall_score: 0.3,
}

// ============================================================================
// Tests
// ============================================================================

describe('aiAnalysisKeys', () => {
  it('generates hierarchical query keys', async () => {
    const { aiAnalysisKeys } = await import('@/hooks/use-ai-analysis')

    expect(aiAnalysisKeys.all).toEqual(['ai-analysis'])
    expect(aiAnalysisKeys.suggestion('ds-1', 'ep-1')).toEqual([
      'ai-analysis',
      'suggestion',
      'ds-1',
      'ep-1',
      undefined,
    ])
    expect(aiAnalysisKeys.trajectory('ds-1', 'ep-1', 'data')).toEqual([
      'ai-analysis',
      'trajectory',
      'ds-1',
      'ep-1',
      'data',
    ])
    expect(aiAnalysisKeys.anomalies('ds-1', 'ep-1')).toEqual([
      'ai-analysis',
      'anomalies',
      'ds-1',
      'ep-1',
      undefined,
    ])
  })
})

// ============================================================================
// useAISuggestion
// ============================================================================

describe('useAISuggestion', () => {
  let wrapper: ReturnType<typeof createQueryWrapper>

  beforeEach(() => {
    wrapper = createQueryWrapper()
    mockGetAnnotationSuggestion.mockReset()
    mockAnalyzeTrajectory.mockReset()
    mockDetectAnomalies.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetches AI suggestion when trajectory has >= 3 positions', async () => {
    mockGetAnnotationSuggestion.mockResolvedValue(suggestionResponse)
    const { useAISuggestion } = await import('@/hooks/use-ai-analysis')
    const { result } = renderHook(
      () =>
        useAISuggestion({
          datasetId: 'ds-1',
          episodeId: 'ep-1',
          trajectoryData: suggestionRequest,
        }),
      { wrapper },
    )

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(result.current.data).toEqual(suggestionResponse)
    expect(mockGetAnnotationSuggestion).toHaveBeenCalledWith(suggestionRequest)
  })

  it('is disabled when trajectory has fewer than 3 positions', async () => {
    const { useAISuggestion } = await import('@/hooks/use-ai-analysis')
    const { result } = renderHook(
      () =>
        useAISuggestion({
          datasetId: 'ds-1',
          episodeId: 'ep-1',
          trajectoryData: { ...suggestionRequest, positions: shortPositions },
        }),
      { wrapper },
    )

    expect(result.current.fetchStatus).toBe('idle')
    expect(mockGetAnnotationSuggestion).not.toHaveBeenCalled()
  })

  it('is disabled when trajectoryData is undefined', async () => {
    const { useAISuggestion } = await import('@/hooks/use-ai-analysis')
    const { result } = renderHook(
      () =>
        useAISuggestion({
          datasetId: 'ds-1',
          episodeId: 'ep-1',
          trajectoryData: undefined,
        }),
      { wrapper },
    )

    expect(result.current.fetchStatus).toBe('idle')
  })

  it('is disabled when enabled is false', async () => {
    const { useAISuggestion } = await import('@/hooks/use-ai-analysis')
    const { result } = renderHook(
      () =>
        useAISuggestion({
          datasetId: 'ds-1',
          episodeId: 'ep-1',
          trajectoryData: suggestionRequest,
          enabled: false,
        }),
      { wrapper },
    )

    expect(result.current.fetchStatus).toBe('idle')
  })
})

// ============================================================================
// useTrajectoryAnalysis
// ============================================================================

describe('useTrajectoryAnalysis', () => {
  let wrapper: ReturnType<typeof createQueryWrapper>

  beforeEach(() => {
    wrapper = createQueryWrapper()
    mockAnalyzeTrajectory.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetches trajectory analysis when trajectory has >= 3 positions', async () => {
    mockAnalyzeTrajectory.mockResolvedValue(trajectoryMetrics)
    const { useTrajectoryAnalysis } = await import('@/hooks/use-ai-analysis')
    const { result } = renderHook(
      () =>
        useTrajectoryAnalysis({
          datasetId: 'ds-1',
          episodeId: 'ep-1',
          trajectoryData,
        }),
      { wrapper },
    )

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(result.current.data).toEqual(trajectoryMetrics)
  })

  it('is disabled when trajectory has fewer than 3 positions', async () => {
    const { useTrajectoryAnalysis } = await import('@/hooks/use-ai-analysis')
    const { result } = renderHook(
      () =>
        useTrajectoryAnalysis({
          datasetId: 'ds-1',
          episodeId: 'ep-1',
          trajectoryData: { ...trajectoryData, positions: shortPositions },
        }),
      { wrapper },
    )

    expect(result.current.fetchStatus).toBe('idle')
  })
})

// ============================================================================
// useAnomalyDetection
// ============================================================================

describe('useAnomalyDetection', () => {
  let wrapper: ReturnType<typeof createQueryWrapper>

  beforeEach(() => {
    wrapper = createQueryWrapper()
    mockDetectAnomalies.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetches anomaly detection when trajectory has >= 3 positions', async () => {
    mockDetectAnomalies.mockResolvedValue(anomalyResponse)
    const { useAnomalyDetection } = await import('@/hooks/use-ai-analysis')
    const { result } = renderHook(
      () =>
        useAnomalyDetection({
          datasetId: 'ds-1',
          episodeId: 'ep-1',
          trajectoryData: anomalyRequest,
        }),
      { wrapper },
    )

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(result.current.data).toEqual(anomalyResponse)
  })

  it('is disabled when trajectoryData is undefined', async () => {
    const { useAnomalyDetection } = await import('@/hooks/use-ai-analysis')
    const { result } = renderHook(
      () =>
        useAnomalyDetection({
          datasetId: 'ds-1',
          episodeId: 'ep-1',
          trajectoryData: undefined,
        }),
      { wrapper },
    )

    expect(result.current.fetchStatus).toBe('idle')
  })
})

// ============================================================================
// useRequestAISuggestion
// ============================================================================

describe('useRequestAISuggestion', () => {
  let wrapper: ReturnType<typeof createQueryWrapper>

  beforeEach(() => {
    wrapper = createQueryWrapper()
    mockGetAnnotationSuggestion.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('triggers mutation with suggestion request data', async () => {
    mockGetAnnotationSuggestion.mockResolvedValue(suggestionResponse)
    const { useRequestAISuggestion } = await import('@/hooks/use-ai-analysis')
    const { result } = renderHook(() => useRequestAISuggestion(), { wrapper })

    result.current.mutate(suggestionRequest)

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(result.current.data).toEqual(suggestionResponse)
    expect(mockGetAnnotationSuggestion).toHaveBeenCalledWith(suggestionRequest, expect.anything())
  })

  it('reports error when mutation fails', async () => {
    mockGetAnnotationSuggestion.mockRejectedValue(new Error('AI service unavailable'))
    const { useRequestAISuggestion } = await import('@/hooks/use-ai-analysis')
    const { result } = renderHook(() => useRequestAISuggestion(), { wrapper })

    result.current.mutate(suggestionRequest)

    await waitFor(() => {
      expect(result.current.isError).toBe(true)
    })

    expect(result.current.error?.message).toBe('AI service unavailable')
  })
})
