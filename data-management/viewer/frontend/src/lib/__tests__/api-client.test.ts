import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  _resetCsrfToken,
  ApiClientError,
  deleteAnnotations,
  fetchAnnotations,
  fetchAnnotationSummary,
  fetchCacheStats,
  fetchCapabilities,
  fetchDataset,
  fetchDatasets,
  fetchEpisode,
  fetchEpisodes,
  mutationHeaders,
  saveAnnotation,
  triggerAutoAnalysis,
  warmCache,
} from '../api-client'

const mockFetch = vi.fn()

/** Mock a successful CSRF token response followed by the API response. */
function mockMutationFetch(apiResponse: ReturnType<typeof jsonResponse>) {
  mockFetch
    .mockResolvedValueOnce(jsonResponse({ csrf_token: 'test-token' }))
    .mockResolvedValueOnce(apiResponse)
}

beforeEach(() => {
  mockFetch.mockReset()
  _resetCsrfToken()
  vi.stubGlobal('fetch', mockFetch)
})

afterEach(() => {
  vi.restoreAllMocks()
})

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    headers: { 'content-type': 'application/json' },
  })
}

describe('ApiClientError', () => {
  it('captures code, status, and details', () => {
    const err = new ApiClientError('not found', 'NOT_FOUND', 404, { id: '1' })
    expect(err.message).toBe('not found')
    expect(err.code).toBe('NOT_FOUND')
    expect(err.status).toBe(404)
    expect(err.details).toEqual({ id: '1' })
    expect(err.name).toBe('ApiClientError')
  })
})

describe('fetchDatasets', () => {
  it('calls GET /api/datasets and returns data', async () => {
    const datasets = [{ id: 'ds-1', name: 'Test' }]
    mockFetch.mockResolvedValueOnce(jsonResponse(datasets))

    const result = await fetchDatasets()
    expect(result).toEqual(datasets)
    expect(mockFetch).toHaveBeenCalledWith('/api/datasets', { headers: {} })
  })

  it('throws ApiClientError on failure', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ code: 'SERVER_ERROR', message: 'boom' }, 500))

    await expect(fetchDatasets()).rejects.toThrow(ApiClientError)
  })
})

describe('fetchDataset', () => {
  it('calls GET /api/datasets/:id', async () => {
    const ds = { id: 'ds-1', name: 'Test' }
    mockFetch.mockResolvedValueOnce(jsonResponse(ds))

    const result = await fetchDataset('ds-1')
    expect(result).toEqual(ds)
    expect(mockFetch).toHaveBeenCalledWith('/api/datasets/ds-1', { headers: {} })
  })
})

describe('fetchEpisodes', () => {
  it('builds query params from options', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse([]))

    await fetchEpisodes('ds-1', {
      offset: 10,
      limit: 20,
      hasAnnotations: true,
      taskIndex: 2,
    })

    const url = mockFetch.mock.calls[0][0] as string
    expect(url).toContain('offset=10')
    expect(url).toContain('limit=20')
    expect(url).toContain('has_annotations=true')
    expect(url).toContain('task_index=2')
  })

  it('transforms snake_case keys to camelCase', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse([
        {
          episode_index: 0,
          frame_count: 100,
          task_index: 0,
          has_annotations: false,
        },
      ]),
    )

    const result = await fetchEpisodes('ds-1')
    expect(result[0]).toHaveProperty('episodeIndex', 0)
    expect(result[0]).toHaveProperty('frameCount', 100)
    expect(result[0]).toHaveProperty('hasAnnotations', false)
  })

  it('calls without query params when no options', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse([]))

    await fetchEpisodes('ds-1')
    expect(mockFetch).toHaveBeenCalledWith('/api/datasets/ds-1/episodes', { headers: {} })
  })
})

describe('fetchEpisode', () => {
  it('calls GET /api/datasets/:id/episodes/:index and transforms keys', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        meta: { episode_index: 5, frame_count: 42 },
        video_urls: { front_cam: '/v.mp4' },
        trajectory_data: [],
      }),
    )

    const result = await fetchEpisode('ds-1', 5)
    expect(result.meta).toHaveProperty('episodeIndex', 5)
    expect(result).toHaveProperty('videoUrls')
    expect(result).toHaveProperty('trajectoryData')
  })
})

describe('fetchAnnotations', () => {
  it('calls GET annotations endpoint', async () => {
    const data = { schemaVersion: '1.0', annotations: [] }
    mockFetch.mockResolvedValueOnce(jsonResponse(data))

    const result = await fetchAnnotations('ds-1', 0)
    expect(result).toEqual(data)
    expect(mockFetch).toHaveBeenCalledWith('/api/datasets/ds-1/episodes/0/annotations', {
      headers: {},
    })
  })
})

describe('saveAnnotation', () => {
  it('calls PUT with annotation body', async () => {
    const annotation = { annotatorId: 'u1' }
    mockMutationFetch(jsonResponse({ success: true }))

    await saveAnnotation('ds-1', 0, annotation as never)

    const apiCall = mockFetch.mock.calls[1]
    expect(apiCall[0]).toBe('/api/datasets/ds-1/episodes/0/annotations')
    expect(apiCall[1]).toMatchObject({
      method: 'PUT',
      body: JSON.stringify(annotation),
    })
  })
})

describe('deleteAnnotations', () => {
  it('calls DELETE without annotatorId', async () => {
    mockMutationFetch(jsonResponse({ deleted: true, episodeIndex: 0 }))

    await deleteAnnotations('ds-1', 0)
    const apiCall = mockFetch.mock.calls[1]
    expect(apiCall[0]).toBe('/api/datasets/ds-1/episodes/0/annotations')
    expect(apiCall[1]).toMatchObject({ method: 'DELETE' })
  })

  it('includes annotator_id query param when provided', async () => {
    mockMutationFetch(jsonResponse({ deleted: true, episodeIndex: 0 }))

    await deleteAnnotations('ds-1', 0, 'u1')
    const url = mockFetch.mock.calls[1][0] as string
    expect(url).toContain('annotator_id=u1')
  })
})

describe('triggerAutoAnalysis', () => {
  it('calls POST auto-analysis endpoint', async () => {
    const analysis = { episodeIndex: 0, suggestedRating: 4 }
    mockMutationFetch(jsonResponse(analysis))

    const result = await triggerAutoAnalysis('ds-1', 0)
    expect(result).toEqual(analysis)
    const apiCall = mockFetch.mock.calls[1]
    expect(apiCall[0]).toBe('/api/datasets/ds-1/episodes/0/annotations/auto')
    expect(apiCall[1]).toMatchObject({ method: 'POST' })
  })
})

describe('fetchAnnotationSummary', () => {
  it('calls GET summary endpoint', async () => {
    const summary = { datasetId: 'ds-1', totalEpisodes: 100 }
    mockFetch.mockResolvedValueOnce(jsonResponse(summary))

    const result = await fetchAnnotationSummary('ds-1')
    expect(result).toEqual(summary)
    expect(mockFetch).toHaveBeenCalledWith('/api/datasets/ds-1/annotations/summary', {
      headers: {},
    })
  })
})

describe('error handling', () => {
  it('creates ApiClientError from JSON error response', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ code: 'DATASET_NOT_FOUND', message: 'Dataset not found' }, 404),
    )

    try {
      await fetchDataset('missing')
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).toBeInstanceOf(ApiClientError)
      const apiErr = err as ApiClientError
      expect(apiErr.code).toBe('DATASET_NOT_FOUND')
      expect(apiErr.status).toBe(404)
    }
  })

  it('handles non-JSON error responses', async () => {
    mockFetch.mockResolvedValueOnce(
      new Response('not json body', {
        status: 500,
        statusText: 'Internal Server Error',
        headers: { 'content-type': 'text/plain' },
      }),
    )

    try {
      await fetchDatasets()
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).toBeInstanceOf(ApiClientError)
      const apiErr = err as ApiClientError
      expect(apiErr.code).toBe('UNKNOWN_ERROR')
      expect(apiErr.message).toBe('Internal Server Error')
    }
  })
})

describe('CSRF token failures', () => {
  it('rejects mutationHeaders when the CSRF endpoint fails', async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 503, statusText: 'Service Unavailable' })

    await expect(mutationHeaders()).rejects.toThrow(/CSRF token/i)
  })

  it('rejects mutationHeaders when the CSRF fetch network errors', async () => {
    mockFetch.mockRejectedValueOnce(new Error('network down'))

    await expect(mutationHeaders()).rejects.toThrow(/network down/i)
  })
})

describe('fetchCapabilities', () => {
  it('GETs /api/datasets/:id/capabilities and camelCases the response', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ supports_annotations: true }))

    const result = await fetchCapabilities('ds-1')
    expect(result).toEqual({ supportsAnnotations: true })
    expect(mockFetch).toHaveBeenCalledWith('/api/datasets/ds-1/capabilities', { headers: {} })
  })
})

describe('fetchCacheStats', () => {
  it('GETs /api/datasets/cache/stats and camelCases the response', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ total_bytes: 100, max_memory_bytes: 200 }))

    const result = await fetchCacheStats()
    expect(result).toEqual({ totalBytes: 100, maxMemoryBytes: 200 })
  })
})

describe('warmCache', () => {
  it('POSTs to /api/datasets/:id/cache/warm with the count query', async () => {
    mockMutationFetch(jsonResponse({}))

    await warmCache('ds-1', 3)

    expect(mockFetch).toHaveBeenLastCalledWith(
      '/api/datasets/ds-1/cache/warm?count=3',
      expect.objectContaining({ method: 'POST' }),
    )
  })
})
