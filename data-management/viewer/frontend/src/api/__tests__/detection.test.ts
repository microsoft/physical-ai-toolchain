import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { DetectionRequest, EpisodeDetectionSummary } from '@/types/detection'

import { clearDetections, getDetections, runDetection } from '../detection'

vi.mock('@/lib/api-client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api-client')>()
  return {
    ...actual,
    apiRequest: vi.fn(),
    handleResponse: vi.fn(),
    mutationHeaders: vi.fn(),
    requestHeaders: vi.fn(),
  }
})

const { apiRequest, handleResponse, mutationHeaders, requestHeaders } =
  await import('@/lib/api-client')
const mockApiRequest = vi.mocked(apiRequest)
const mockHandleResponse = vi.mocked(handleResponse)
const mockMutationHeaders = vi.mocked(mutationHeaders)
const mockRequestHeaders = vi.mocked(requestHeaders)
const mockFetch = vi.fn()

beforeEach(() => {
  mockFetch.mockReset()
  mockApiRequest.mockReset()
  mockHandleResponse.mockReset()
  mockMutationHeaders.mockReset()
  mockRequestHeaders.mockReset()
  mockMutationHeaders.mockResolvedValue({ 'X-CSRF-Token': 'test-token' })
  mockRequestHeaders.mockResolvedValue({ Authorization: 'Bearer test' })
  mockApiRequest.mockImplementation(async (path, init) => {
    const method = init?.method ?? 'GET'
    const baseHeaders = method === 'GET' ? await mockRequestHeaders() : await mockMutationHeaders()
    const response = await mockFetch(`/api${path}`, {
      ...init,
      headers: { ...baseHeaders, ...(init?.headers as Record<string, string> | undefined) },
    })
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

const summary: EpisodeDetectionSummary = {
  totalFrames: 100,
  processedFrames: 100,
} as EpisodeDetectionSummary

describe('runDetection', () => {
  it('POSTs detection request to the episode endpoint', async () => {
    const request: DetectionRequest = { confidence: 0.5, model: 'yolo11n' }
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockResolvedValueOnce(summary)

    const result = await runDetection('ds-1', 7, request)

    expect(result).toBe(summary)
    const [url, init] = mockFetch.mock.calls[0]
    expect(url).toBe('/api/datasets/ds-1/episodes/7/detect')
    expect(init).toMatchObject({
      method: 'POST',
      body: JSON.stringify(request),
    })
    expect(init.headers).toMatchObject({ 'Content-Type': 'application/json' })
  })

  it('defaults the request body to an empty object', async () => {
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockResolvedValueOnce(summary)

    await runDetection('ds-1', 0)

    const [, init] = mockFetch.mock.calls[0]
    expect(init.body).toBe('{}')
  })
})

describe('getDetections', () => {
  it('GETs the cached detections endpoint with auth headers', async () => {
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockResolvedValueOnce(summary)

    const result = await getDetections('ds-1', 3)

    expect(result).toBe(summary)
    expect(mockRequestHeaders).toHaveBeenCalledTimes(1)
    expect(mockFetch).toHaveBeenCalledWith('/api/datasets/ds-1/episodes/3/detections', {
      headers: { Authorization: 'Bearer test' },
    })
  })

  it('returns null when handleResponse resolves null', async () => {
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockResolvedValueOnce(null)

    const result = await getDetections('ds-1', 3)

    expect(result).toBeNull()
  })

  it('preserves an uncached null response through the real API client', async () => {
    const { apiRequest: realApiRequest } =
      await vi.importActual<typeof import('@/lib/api-client')>('@/lib/api-client')
    mockApiRequest.mockImplementationOnce(realApiRequest)
    mockFetch.mockResolvedValueOnce(new Response('null', { status: 200 }))

    const result = await getDetections('ds-1', 3)

    expect(result).toBeNull()
  })

  it('preserves semantic class summary keys while camelCasing the response', async () => {
    const raw = {
      total_frames: 1,
      processed_frames: 1,
      total_detections: 1,
      detections_by_frame: [],
      class_summary: {
        fire_extinguisher: { count: 1, avg_confidence: 0.9 },
      },
    }
    mockApiRequest.mockImplementationOnce(async (_path, _init, transform) => transform!(raw))

    const result = await getDetections('ds-1', 3)

    expect(result?.classSummary).toEqual({
      fire_extinguisher: { count: 1, avgConfidence: 0.9 },
    })
  })

  it('converts detection summaries without class statistics', async () => {
    const raw = {
      total_frames: 1,
      processed_frames: 1,
      total_detections: 0,
      detections_by_frame: [],
      class_summary: null,
    }
    mockApiRequest.mockImplementationOnce(async (_path, _init, transform) => transform!(raw))

    const result = await getDetections('ds-1', 3)

    expect(result).toMatchObject({
      totalFrames: 1,
      processedFrames: 1,
      totalDetections: 0,
      detectionsByFrame: [],
      classSummary: {},
    })
    expect(result?.classSummary).toEqual({})
  })
})

describe('clearDetections', () => {
  it('DELETEs the detections endpoint with mutation headers', async () => {
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockResolvedValueOnce({ cleared: true })

    const result = await clearDetections('ds-1', 9)

    expect(result).toEqual({ cleared: true })
    expect(mockFetch).toHaveBeenCalledWith('/api/datasets/ds-1/episodes/9/detections', {
      headers: { 'X-CSRF-Token': 'test-token' },
      method: 'DELETE',
    })
  })

  it('propagates errors from handleResponse', async () => {
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockRejectedValueOnce(new Error('forbidden'))

    await expect(clearDetections('ds-1', 9)).rejects.toThrow('forbidden')
  })
})
