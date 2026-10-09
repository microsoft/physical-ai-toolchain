import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  installFetchMock,
  jsonResponse,
  mockFetch,
  mockMutationFetch,
} from '@/test-utils/fetch-mocks'

import {
  _resetCsrfToken,
  ApiClientError,
  apiPath,
  apiRequest,
  deleteAnnotations,
  fetchAnnotations,
  fetchAnnotationSummary,
  fetchCacheStats,
  fetchCapabilities,
  fetchDataset,
  fetchDatasets,
  fetchEpisode,
  fetchEpisodes,
  fetchVlmJudgeStatus,
  mutateJudgeDataset,
  mutationFetch,
  mutationHeaders,
  runVlmJudge,
  saveAnnotation,
  submitJudgeJob,
  triggerAutoAnalysis,
  warmCache,
} from '../api-client'
import {
  clearDiagnosticEvents,
  disableDiagnostics,
  enableDiagnostics,
  readDiagnosticEvents,
} from '../playback-diagnostics'

beforeEach(() => {
  installFetchMock({ csrf: false })
  _resetCsrfToken()
})

afterEach(() => {
  disableDiagnostics()
  clearDiagnosticEvents()
  vi.useRealTimers()
  vi.restoreAllMocks()
})
describe('ApiClientError', () => {
  it('captures code, status, and details', () => {
    const err = new ApiClientError('not found', 'NOT_FOUND', 404, { id: '1' })
    expect(err.message).toBe('not found')
    expect(err.code).toBe('NOT_FOUND')
    expect(err.status).toBe(404)
    expect(err.details).toEqual({ id: '1' })
    expect(err.name).toBe('ApiClientError')
  })

  describe('canonical transport', () => {
    it('builds all backend paths from the shared API base', () => {
      expect(apiPath('/datasets')).toBe('/api/datasets')
      expect(apiPath('datasets/ds-1')).toBe('/api/datasets/ds-1')
    })

    it('attaches request headers and camelCases successful JSON responses', async () => {
      mockFetch.mockResolvedValueOnce(
        jsonResponse({
          dataset_id: 'ds-1',
          nested_value: { frame_count: 12 },
        }),
      )

      await expect(
        apiRequest<{ datasetId: string; nestedValue: { frameCount: number } }>('/datasets/ds-1'),
      ).resolves.toEqual({
        datasetId: 'ds-1',
        nestedValue: { frameCount: 12 },
      })
      expect(mockFetch).toHaveBeenCalledWith('/api/datasets/ds-1', { headers: {} })
    })

    it('does not expose FastAPI detail text for server errors', async () => {
      mockFetch.mockResolvedValueOnce(
        jsonResponse({ detail: '/srv/data/private: permission denied' }, 500),
      )

      await expect(apiRequest('/datasets/ds-1')).rejects.toMatchObject({
        name: 'ApiClientError',
        code: 'HTTP_500',
        status: 500,
        message: 'The server could not complete the request',
      })
    })

    it('uses a generic message for 5xx even with a known code and diagnostic details', async () => {
      mockFetch.mockResolvedValueOnce(
        jsonResponse(
          {
            code: 'DATASET_NOT_FOUND',
            message: '/srv/data/private: permission denied',
            details: { path: '/srv/data/private' },
          },
          500,
        ),
      )
      await expect(apiRequest('/datasets/ds-1')).rejects.toMatchObject({
        message: 'The server could not complete the request',
        details: undefined,
      })
    })
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
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ code: 'SERVER_ERROR', message: 'boom' }, { status: 500 }),
    )

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
  it('does not start a read that is already cancelled', async () => {
    vi.useFakeTimers()
    const controller = new AbortController()
    controller.abort()
    await expect(fetchEpisode('ds-1', 5, controller.signal)).rejects.toMatchObject({
      name: 'AbortError',
    })
    expect(mockFetch).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('keeps the deadline active until the response body is read', async () => {
    vi.useFakeTimers()
    const response = jsonResponse({})
    vi.spyOn(response, 'json').mockImplementation(() => new Promise(() => {}))
    mockFetch.mockResolvedValueOnce(response)
    const failure = expect(fetchEpisode('ds-1', 5)).rejects.toMatchObject({
      code: 'EPISODE_TIMEOUT',
    })
    await vi.advanceTimersByTimeAsync(60_000)
    await failure
    expect(response.json).toHaveBeenCalledOnce()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('bounds a stalled episode read and permits a fresh retry without leaking timers', async () => {
    vi.useFakeTimers()
    enableDiagnostics('workspace')
    mockFetch.mockImplementationOnce(() => new Promise<Response>(() => {}))
    const request = fetchEpisode('ds-1', 5)
    const failure = expect(request).rejects.toMatchObject({ code: 'EPISODE_TIMEOUT', status: 0 })
    await vi.advanceTimersByTimeAsync(60_000)
    await failure
    expect(mockFetch.mock.calls[0][1]?.signal?.aborted).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
    mockFetch.mockResolvedValueOnce(jsonResponse({ meta: { index: 5 } }))
    await expect(fetchEpisode('ds-1', 5)).resolves.toMatchObject({ meta: { index: 5 } })
    expect(vi.getTimerCount()).toBe(0)
    expect(readDiagnosticEvents('workspace').map((event) => event.data?.outcome)).toEqual([
      'timeout',
      'success',
    ])
  })

  it('cancels an obsolete read without waiting for the deadline', async () => {
    vi.useFakeTimers()
    enableDiagnostics('workspace')
    const controller = new AbortController()
    mockFetch.mockImplementationOnce(() => new Promise<Response>(() => {}))
    const request = fetchEpisode('ds-1', 5, controller.signal)
    const failure = expect(request).rejects.toMatchObject({ name: 'AbortError' })
    await vi.advanceTimersByTimeAsync(0)
    controller.abort()
    await failure
    expect(mockFetch.mock.calls[0][1]?.signal?.aborted).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
    expect(readDiagnosticEvents('workspace')[0].data?.outcome).toBe('cancelled')
  })

  it('records safe HTTP failure details and duration without provider content', async () => {
    enableDiagnostics('workspace')
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ code: 'private-provider-detail', detail: 'private-body' }, 503),
    )
    await expect(fetchEpisode('ds-1', 5)).rejects.toMatchObject({ status: 503 })
    expect(readDiagnosticEvents('workspace')[0]).toMatchObject({
      type: 'episode-request-completed',
      data: { outcome: 'http-error', status: 503, durationMs: expect.any(Number) },
    })
    expect(JSON.stringify(readDiagnosticEvents('workspace'))).not.toContain('private')
  })

  it('calls GET /api/datasets/:id/episodes/:index and transforms keys', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        meta: { episode_index: 5, frame_count: 42 },
        video_urls: { front_cam: '/v.mp4' },
        trajectory_data: [],
      }),
    )

    const result = await fetchEpisode('ds-1', 5)
    expect(mockFetch).toHaveBeenCalledWith('/api/datasets/ds-1/episodes/5', {
      cache: 'no-store',
      headers: {},
      signal: expect.any(AbortSignal),
    })
    expect(result.meta).toHaveProperty('episodeIndex', 5)
    expect(result).toHaveProperty('videoUrls')
    expect(result).toHaveProperty('trajectoryData')
  })

  it('preserves video_urls camera keys verbatim (no camelCasing)', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        meta: { episode_index: 0 },
        video_urls: {
          'observation.images.cam_0': '/v0.mp4',
          'observation.images.wrist_cam': '/v1.mp4',
        },
        trajectory_data: [],
      }),
    )

    const result = await fetchEpisode('ds-1', 0)
    expect(result.videoUrls).toEqual({
      'observation.images.cam_0': '/v0.mp4',
      'observation.images.wrist_cam': '/v1.mp4',
    })
  })

  it('preserves video_time_windows camera keys and tuple values', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        meta: { episode_index: 0 },
        video_urls: { 'observation.images.wrist_cam': '/v.mp4' },
        video_time_windows: {
          'observation.images.wrist_cam': [1.5, 4.25],
          'observation.images.overhead.cam': [0, 10],
        },
        trajectory_data: [],
      }),
    )

    const result = await fetchEpisode('ds-1', 0)
    expect(result.videoTimeWindows).toEqual({
      'observation.images.wrist_cam': [1.5, 4.25],
      'observation.images.overhead.cam': [0, 10],
    })
  })

  it('omits videoTimeWindows when the backend payload has no window map', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        meta: { episode_index: 0 },
        video_urls: { front: '/v.mp4' },
        trajectory_data: [],
      }),
    )

    const result = await fetchEpisode('ds-1', 0)
    expect(result.videoTimeWindows).toBeUndefined()
  })

  it('preserves trajectory variable keys with dots and underscores', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        meta: { episode_index: 0 },
        video_urls: {},
        trajectory_data: [
          {
            timestamp: 0,
            frame: 0,
            variables: {
              'observation.gripper.is_closed': 1,
              'observation.joint_0.position': 0.42,
            },
          },
        ],
      }),
    )

    const result = await fetchEpisode('ds-1', 0)
    const variables = (result.trajectoryData[0] as unknown as { variables: Record<string, number> })
      .variables
    expect(variables).toEqual({
      'observation.gripper.is_closed': 1,
      'observation.joint_0.position': 0.42,
    })
  })
})

describe('fetchAnnotations', () => {
  it.each(['read', 'save'])(
    'preserves provenance identities and opaque values on %s',
    async (operation) => {
      const response = jsonResponse(
        {
          dataset_id: 'ds',
          episode_index: 0,
          annotations: [],
          provenance: {
            user_scope: {
              schema_version: '1.0.0',
              acceptances: { result_key: ['user_scope'] },
              withdrawn: [],
              contributions: [
                { id: 'result_key', author_id: 'user_scope', value: { camera_name: 'front' } },
              ],
            },
          },
        },
        { headers: { ETag: '"revision"' } },
      )
      if (operation === 'read') mockFetch.mockResolvedValueOnce(response)
      else mockMutationFetch(response)

      const result =
        operation === 'read'
          ? await fetchAnnotations('ds', 0)
          : await saveAnnotation('ds', 0, { annotatorId: 'user_scope' } as never, {
              createOnly: true,
            })

      expect(result.data).toMatchObject({
        provenance: {
          user_scope: {
            schemaVersion: '1.0.0',
            acceptances: { result_key: ['user_scope'] },
            contributions: [
              { id: 'result_key', authorId: 'user_scope', value: { camera_name: 'front' } },
            ],
          },
        },
      })
    },
  )

  it('returns the camelCased annotation response with its revision', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse(
        {
          schema_version: '1.0',
          episode_index: 0,
          dataset_id: 'ds-1',
          annotations: [
            {
              annotator_id: 'u1',
              language_instruction: {
                instruction: 'Pick up the cube',
                subtask_instructions: [
                  { id: 'reach', text: 'Reach' },
                  { id: 'grasp', text: 'Grasp' },
                ],
              },
            },
          ],
        },
        { headers: { ETag: '"revision-one"' } },
      ),
    )

    const result = await fetchAnnotations('ds-1', 0)
    expect(result).toEqual({
      data: {
        schemaVersion: '1.0',
        episodeIndex: 0,
        datasetId: 'ds-1',
        annotations: [
          {
            annotatorId: 'u1',
            languageInstruction: {
              instruction: 'Pick up the cube',
              subtaskInstructions: [
                { id: 'reach', text: 'Reach' },
                { id: 'grasp', text: 'Grasp' },
              ],
            },
          },
        ],
      },
      etag: '"revision-one"',
    })
    expect(mockFetch).toHaveBeenCalledWith('/api/datasets/ds-1/episodes/0/annotations', {
      headers: {},
    })
  })
})

describe('saveAnnotation', () => {
  it('calls PUT with a create-only precondition and converts the response', async () => {
    const annotation = {
      annotatorId: 'u1',
      languageInstruction: {
        instruction: 'Pick up the cube',
        subtaskInstructions: [
          { id: 'reach', text: 'Reach' },
          { id: 'grasp', text: 'Grasp' },
        ],
      },
    }
    mockMutationFetch(
      jsonResponse(
        {
          schema_version: '1.0',
          episode_index: 0,
          dataset_id: 'ds-1',
          annotations: [
            {
              annotator_id: 'u1',
              language_instruction: {
                instruction: 'Pick up the cube',
                subtask_instructions: [
                  { id: 'reach', text: 'Reach' },
                  { id: 'grasp', text: 'Grasp' },
                ],
              },
            },
          ],
        },
        { headers: { ETag: '"created"' } },
      ),
    )

    const result = await saveAnnotation('ds-1', 0, annotation as never, { createOnly: true })

    const apiCall = mockFetch.mock.calls[1]
    expect(apiCall[0]).toBe('/api/datasets/ds-1/episodes/0/annotations')
    expect(apiCall[1]).toMatchObject({
      method: 'PUT',
      body: JSON.stringify({
        annotator_id: 'u1',
        language_instruction: {
          instruction: 'Pick up the cube',
          subtask_instructions: [
            { id: 'reach', text: 'Reach' },
            { id: 'grasp', text: 'Grasp' },
          ],
        },
      }),
    })
    expect(apiCall[1].headers).toHaveProperty('If-None-Match', '*')
    expect(result.etag).toBe('"created"')
    expect(result.data.annotations[0]?.languageInstruction?.instruction).toBe('Pick up the cube')
  })

  it('sends If-Match for an existing annotation resource', async () => {
    const annotation = { annotatorId: 'u1' }
    mockMutationFetch(jsonResponse({ success: true }, { headers: { ETag: '"updated"' } }))

    await saveAnnotation('ds-1', 0, annotation as never, { etag: '"revision-one"' })

    expect(mockFetch.mock.calls[1][1].headers).toHaveProperty('If-Match', '"revision-one"')
  })

  it('preserves the current ETag from a 412 conflict', async () => {
    const annotation = { annotatorId: 'u1' }
    mockMutationFetch(
      jsonResponse(
        {
          code: 'PRECONDITION_FAILED',
          message: 'Resource revision precondition failed',
          details: { currentEtag: '"revision-two"' },
        },
        { status: 412, headers: { ETag: '"revision-two"' } },
      ),
    )

    await expect(
      saveAnnotation('ds-1', 0, annotation as never, { etag: '"revision-one"' }),
    ).rejects.toMatchObject({
      name: 'ApiClientError',
      status: 412,
      details: { currentEtag: '"revision-two"' },
    })
  })
})

describe('deleteAnnotations', () => {
  it('calls DELETE without annotatorId', async () => {
    mockMutationFetch(jsonResponse({ deleted: true, episodeIndex: 0 }))

    await deleteAnnotations('ds-1', 0, '"revision-one"')
    const apiCall = mockFetch.mock.calls[1]
    expect(apiCall[0]).toBe('/api/datasets/ds-1/episodes/0/annotations')
    expect(apiCall[1]).toMatchObject({ method: 'DELETE' })
    expect(apiCall[1].headers).toHaveProperty('If-Match', '"revision-one"')
  })

  it('does not expose an annotator owner selector', async () => {
    mockMutationFetch(jsonResponse({ deleted: true, episodeIndex: 0 }))

    await deleteAnnotations('ds-1', 0, '"revision-one"')

    const url = mockFetch.mock.calls[1][0] as string
    expect(url).not.toContain('annotator_id')
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
      jsonResponse({ code: 'DATASET_NOT_FOUND', message: 'Dataset not found' }, { status: 404 }),
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
      expect(apiErr.code).toBe('HTTP_500')
      expect(apiErr.message).toBe('The server could not complete the request')
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

describe('mutationFetch', () => {
  it('skips CSRF fetch and omits X-CSRF-Token for GET requests', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ ok: true }))

    await mutationFetch('/api/thing')

    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [url, init] = mockFetch.mock.calls[0]
    expect(url).toBe('/api/thing')
    const headers = new Headers((init as RequestInit).headers)
    expect(headers.has('X-CSRF-Token')).toBe(false)
  })

  it('skips CSRF fetch and omits X-CSRF-Token for HEAD requests', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({}))

    await mutationFetch('/api/thing', { method: 'HEAD' })

    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [, init] = mockFetch.mock.calls[0]
    const headers = new Headers((init as RequestInit).headers)
    expect(headers.has('X-CSRF-Token')).toBe(false)
  })

  it.each(['POST', 'PUT', 'DELETE', 'PATCH'])(
    'fetches CSRF and attaches X-CSRF-Token for %s requests',
    async (method) => {
      mockMutationFetch(jsonResponse({ ok: true }))

      await mutationFetch('/api/thing', { method })

      expect(mockFetch).toHaveBeenCalledTimes(2)
      expect(mockFetch.mock.calls[0][0]).toBe('/api/csrf-token')
      const [, init] = mockFetch.mock.calls[1]
      const headers = new Headers((init as RequestInit).headers)
      expect(headers.get('X-CSRF-Token')).toBe('test-csrf-token')
    },
  )

  it('treats lowercase method names as their canonical uppercase form', async () => {
    mockMutationFetch(jsonResponse({ ok: true }))

    await mutationFetch('/api/thing', { method: 'post' })

    expect(mockFetch).toHaveBeenCalledTimes(2)
    expect(mockFetch.mock.calls[0][0]).toBe('/api/csrf-token')
    const [, init] = mockFetch.mock.calls[1]
    const headers = new Headers((init as RequestInit).headers)
    expect(headers.get('X-CSRF-Token')).toBe('test-csrf-token')
  })

  it('lets caller-provided headers win on key collision with X-CSRF-Token', async () => {
    mockMutationFetch(jsonResponse({ ok: true }))

    await mutationFetch('/api/thing', {
      method: 'POST',
      headers: { 'X-CSRF-Token': 'caller-override' },
    })

    const [, init] = mockFetch.mock.calls[1]
    const headers = new Headers((init as RequestInit).headers)
    expect(headers.get('X-CSRF-Token')).toBe('caller-override')
  })

  it('lets differently-cased caller headers replace generated headers', async () => {
    mockMutationFetch(jsonResponse({ ok: true }))

    await mutationFetch('/api/thing', {
      method: 'POST',
      headers: { 'x-csrf-token': 'caller-override' },
    })

    const [, init] = mockFetch.mock.calls[1]
    const headers = new Headers((init as RequestInit).headers)
    expect(headers.get('X-CSRF-Token')).toBe('caller-override')
    expect([...headers.keys()].filter((name) => name === 'x-csrf-token')).toHaveLength(1)
  })

  it('declares JSON for string bodies while keeping CSRF and caller overrides', async () => {
    mockMutationFetch(jsonResponse({ ok: true }))
    await mutationFetch('/api/thing', { method: 'POST', body: '{"a":1}' })
    const headers = new Headers((mockFetch.mock.calls[1][1] as RequestInit).headers)
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(headers.get('X-CSRF-Token')).toBe('test-csrf-token')

    mockFetch.mockResolvedValueOnce(jsonResponse({ ok: true }))
    await mutationFetch('/api/thing', {
      method: 'POST',
      body: 'plain',
      headers: { 'content-type': 'text/plain' },
    })
    expect(
      new Headers((mockFetch.mock.calls[2][1] as RequestInit).headers).get('Content-Type'),
    ).toBe('text/plain')
  })

  it('leaves FormData and bodiless requests without a JSON content type', async () => {
    mockMutationFetch(jsonResponse({ ok: true }))
    await mutationFetch('/api/upload', { method: 'POST', body: new FormData() })
    mockFetch.mockResolvedValueOnce(jsonResponse({ ok: true }))
    await mutationFetch('/api/thing', { method: 'POST' })
    for (const call of mockFetch.mock.calls.slice(1)) {
      expect(new Headers((call[1] as RequestInit).headers).has('Content-Type')).toBe(false)
    }
  })
})

describe('judge JSON mutations', () => {
  it.each([
    [{ kind: 'apply', jobId: 'job-1', indices: [0] }, { episode_indices: [0] }],
    [
      { kind: 'approve', jobId: 'job-1', acknowledgeExceptions: true },
      { acknowledge_exceptions: true },
    ],
    [{ kind: 'cancel', jobId: 'job-1' }, {}],
    [{ kind: 'preview-reset' }, { dataset_id: 'ds-1' }],
  ] as const)('sends %o as an application/json object', async (action, body) => {
    mockMutationFetch(jsonResponse({ id: 'job-1' }, 202))
    await mutateJudgeDataset('ds-1', action as never)
    const init = mockFetch.mock.calls[1][1] as RequestInit
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json')
    expect(JSON.parse(init.body as string)).toEqual(body)
  })

  it('declares JSON when submitting dataset jobs', async () => {
    mockMutationFetch(jsonResponse({ id: 'job-1' }, 202))
    await submitJudgeJob('ds-1', { indices: [0], mode: 'judge' } as never, 'request-1')
    const headers = new Headers((mockFetch.mock.calls[1][1] as RequestInit).headers)
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(headers.get('Idempotency-Key')).toBe('request-1')
  })
})

describe('fetchVlmJudgeStatus', () => {
  it('maps a 404 (router not mounted) to the disabled status', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ detail: 'Not Found' }, { status: 404 }))

    const result = await fetchVlmJudgeStatus('ds-1', 0)
    expect(result.enabled).toBe(false)
    expect(result.result).toBeNull()
    expect(mockFetch).toHaveBeenCalledWith('/api/datasets/ds-1/episodes/0/judge', { headers: {} })
  })

  it('camelCases an enabled status payload', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        enabled: true,
        cached: false,
        judge_model: 'Qwen/Qwen3-VL-4B-Instruct',
        prompt_version: 'outcome-mcq-v1',
        cache_key: null,
        result: null,
      }),
    )

    const result = await fetchVlmJudgeStatus('ds-1', 0)
    expect(result.enabled).toBe(true)
    expect(result.judgeModel).toBe('Qwen/Qwen3-VL-4B-Instruct')
    expect(result.promptVersion).toBe('outcome-mcq-v1')
  })
})

describe('runVlmJudge', () => {
  it('submits durable work and reads its saved result through status polling', async () => {
    mockFetch
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'csrf-1' }))
      .mockResolvedValueOnce(
        jsonResponse({ id: 'job-1', dataset_id: 'ds-1', status: 'queued' }, 202),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          id: 'job-1',
          dataset_id: 'ds-1',
          status: 'succeeded',
          config: { process_method: 'gvl' },
          targets: [
            {
              episode_index: 0,
              status: 'succeeded',
              input: { snapshot_id: 'snapshot-1' },
              cached: true,
              result: {
                episode_id: 'ds-1/episode_000000',
                instruction: 'Pick',
                judge_model: 'Qwen/Qwen3-VL-4B-Instruct',
                prompt_version: 'outcome-mcq-v1',
                n_frames: 6,
                outcome_success: true,
                outcome_confidence: 1,
                outcome_n_valid_votes: 3,
                progress_per_frame: [100],
                voc: 1,
                milestones: [],
                failure_mode: null,
                cached: true,
              },
            },
          ],
        }),
      )

    await expect(runVlmJudge('ds-1', 0, { processMethod: 'gvl' })).resolves.toMatchObject({
      episodeId: 'ds-1/episode_000000',
      cached: true,
    })
    expect(mockFetch).toHaveBeenCalledTimes(3)
    expect(mockFetch).toHaveBeenLastCalledWith(
      '/api/judge/jobs/job-1',
      expect.objectContaining({ cache: 'no-store' }),
    )
  })
})
