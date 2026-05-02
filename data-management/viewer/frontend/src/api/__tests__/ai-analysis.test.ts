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
  handleResponse: vi.fn(),
  mutationHeaders: vi.fn(),
}))

const { handleResponse, mutationHeaders } = await import('@/lib/api-client')
const mockHandleResponse = vi.mocked(handleResponse)
const mockMutationHeaders = vi.mocked(mutationHeaders)
const mockFetch = vi.fn()

beforeEach(() => {
  mockFetch.mockReset()
  mockHandleResponse.mockReset()
  mockMutationHeaders.mockReset()
  mockMutationHeaders.mockResolvedValue({ 'X-CSRF-Token': 'test-token' })
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
      gripper_states: [0],
    }
    const metrics: TrajectoryMetrics = {
      smoothness: 0.9,
      efficiency: 0.8,
      jitter: 0.1,
      hesitation_count: 0,
      correction_count: 0,
      overall_score: 0.85,
      flags: [],
    }
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockResolvedValueOnce(metrics)

    const result = await analyzeTrajectory(data)

    expect(result).toEqual(metrics)
    expect(mockMutationHeaders).toHaveBeenCalledTimes(1)
    expect(mockFetch).toHaveBeenCalledWith('/api/ai/trajectory-analysis', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': 'test-token',
      },
      body: JSON.stringify(data),
    })
    expect(mockHandleResponse).toHaveBeenCalledWith(expect.objectContaining({ ok: true }))
  })

  it('propagates errors from handleResponse', async () => {
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockRejectedValueOnce(new Error('parse failure'))

    await expect(
      analyzeTrajectory({ positions: [], timestamps: [] }),
    ).rejects.toThrow('parse failure')
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
      total_count: 0,
      severity_counts: { low: 0, medium: 0, high: 0 },
    }
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockResolvedValueOnce(response)

    const result = await detectAnomalies(request)

    expect(result).toEqual(response)
    const [url, init] = mockFetch.mock.calls[0]
    expect(url).toBe('/api/ai/anomaly-detection')
    expect(init).toMatchObject({
      method: 'POST',
      body: JSON.stringify(request),
    })
    expect(init.headers).toMatchObject({
      'Content-Type': 'application/json',
      'X-CSRF-Token': 'test-token',
    })
  })
})

describe('clusterEpisodes', () => {
  it('POSTs cluster request and returns the response', async () => {
    const request: ClusterRequest = {
      trajectories: [[[0, 0, 0]]],
      num_clusters: 3,
    }
    const response: ClusterResponse = {
      num_clusters: 3,
      assignments: [],
      cluster_sizes: { '0': 0 },
      silhouette_score: 0.5,
    }
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockResolvedValueOnce(response)

    const result = await clusterEpisodes(request)

    expect(result).toEqual(response)
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/ai/cluster',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify(request),
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
      task_completion_rating: 4,
      trajectory_quality_score: 0.9,
      suggested_flags: [],
      detected_anomalies: [],
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
        body: JSON.stringify(request),
      }),
    )
  })

  it('awaits mutationHeaders for every request', async () => {
    mockFetch.mockResolvedValue(okResponse())
    mockHandleResponse.mockResolvedValue({} as AnnotationSuggestion)

    await getAnnotationSuggestion({ positions: [], timestamps: [] })
    await getAnnotationSuggestion({ positions: [], timestamps: [] })

    expect(mockMutationHeaders).toHaveBeenCalledTimes(2)
  })
})
