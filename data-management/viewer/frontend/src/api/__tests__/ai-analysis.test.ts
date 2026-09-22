import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  AnnotationSuggestion,
  AnomalyDetectionRequest,
  AnomalyDetectionResponse,
  ClusterRequest,
  ClusterResponse,
  SuggestAnnotationRequest,
  TrajectoryData,
  TrajectoryMetrics,
} from '../ai-analysis'
import {
  analyzeTrajectory,
  clusterEpisodes,
  detectAnomalies,
  getAnnotationSuggestion,
} from '../ai-analysis'

vi.mock('@/lib/api-client', () => ({
  apiRequest: vi.fn(),
  handleResponse: vi.fn(),
}))

const { apiRequest, handleResponse } = await import('@/lib/api-client')
const mockApiRequest = vi.mocked(apiRequest)
const mockHandleResponse = vi.mocked(handleResponse)
const mockFetch = vi.fn()

beforeEach(() => {
  mockFetch.mockReset()
  mockApiRequest.mockReset()
  mockHandleResponse.mockReset()
  mockApiRequest.mockImplementation(async (path, init) => {
    const response = await mockFetch(`/api${path}`, init)
    return mockHandleResponse(response)
  })
  vi.stubGlobal('fetch', mockFetch)
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function okResponse(): Response {
  return { ok: true, status: 200, statusText: 'OK' } as Response
}

describe('analyzeTrajectory', () => {
  it('POSTs trajectory data and returns parsed metrics', async () => {
    const data: TrajectoryData = {
      positions: [[0, 0, 0]],
      timestamps: [0],
      gripperStates: [0],
    }
    const metrics: TrajectoryMetrics = {
      smoothness: 0.9,
      normalizedSmoothness: 0.6,
      efficiency: 0.8,
      jitter: 0.1,
      hesitationCount: 0,
      correctionCount: 0,
      overallScore: 0.85,
      flags: [],
    }
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockResolvedValueOnce(metrics)

    const result = await analyzeTrajectory(data)

    expect(result).toEqual(metrics)
    expect(mockFetch).toHaveBeenCalledWith('/api/ai/trajectory-analysis', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        positions: data.positions,
        timestamps: data.timestamps,
        gripper_states: data.gripperStates,
      }),
    })
    expect(mockHandleResponse).toHaveBeenCalledWith(expect.objectContaining({ ok: true }))
  })

  it('propagates errors from handleResponse', async () => {
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockRejectedValueOnce(new Error('parse failure'))

    await expect(analyzeTrajectory({ positions: [], timestamps: [] })).rejects.toThrow(
      'parse failure',
    )
  })
})

describe('detectAnomalies', () => {
  it('POSTs anomaly detection request with auth headers', async () => {
    const request: AnomalyDetectionRequest = {
      positions: [[0, 0, 0]],
      timestamps: [0],
    }
    const response: AnomalyDetectionResponse = {
      anomalies: [],
      totalCount: 0,
      severityCounts: { low: 0, medium: 0, high: 0 },
    }
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockResolvedValueOnce(response)

    const result = await detectAnomalies(request)

    expect(result).toEqual(response)
    const [url, init] = mockFetch.mock.calls[0]
    expect(url).toBe('/api/ai/anomaly-detection')
    expect(init).toMatchObject({
      method: 'POST',
      body: JSON.stringify({
        positions: request.positions,
        timestamps: request.timestamps,
      }),
    })
    expect(init.headers).toMatchObject({ 'Content-Type': 'application/json' })
  })
})

describe('clusterEpisodes', () => {
  it('POSTs cluster request and returns the response', async () => {
    const request: ClusterRequest = {
      trajectories: [[[0, 0, 0]]],
      numClusters: 3,
    }
    const response: ClusterResponse = {
      numClusters: 3,
      assignments: [],
      clusterSizes: { '0': 0 },
      silhouetteScore: 0.5,
    }
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockResolvedValueOnce(response)

    const result = await clusterEpisodes(request)

    expect(result).toEqual(response)
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/ai/cluster',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ trajectories: request.trajectories, num_clusters: 3 }),
      }),
    )
  })
})

describe('getAnnotationSuggestion', () => {
  it('POSTs suggestion request and returns the suggestion', async () => {
    const request: SuggestAnnotationRequest = {
      positions: [[0, 0, 0]],
      timestamps: [0],
    }
    const suggestion: AnnotationSuggestion = {
      taskCompletionRating: 4,
      trajectoryQualityScore: 0.9,
      suggestedFlags: [],
      detectedAnomalies: [],
      confidence: 0.95,
      reasoning: 'looks good',
    }
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockResolvedValueOnce(suggestion)

    const result = await getAnnotationSuggestion(request)

    expect(result).toEqual(suggestion)
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/ai/suggest-annotation',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          positions: request.positions,
          timestamps: request.timestamps,
        }),
      }),
    )
  })

  it('routes every request through the canonical client', async () => {
    mockFetch.mockResolvedValue(okResponse())
    mockHandleResponse.mockResolvedValue({} as AnnotationSuggestion)

    await getAnnotationSuggestion({ positions: [], timestamps: [] })
    await getAnnotationSuggestion({ positions: [], timestamps: [] })

    expect(mockApiRequest).toHaveBeenCalledTimes(2)
  })
})
