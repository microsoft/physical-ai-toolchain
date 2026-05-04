import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { DetectionRequest, EpisodeDetectionSummary } from '@/types/detection'

import { clearDetections, getDetections, runDetection } from '../detection'

vi.mock('@/lib/api-client', () => ({
  handleResponse: vi.fn(),
  mutationHeaders: vi.fn(),
  requestHeaders: vi.fn(),
}))

const { handleResponse, mutationHeaders, requestHeaders } = await import('@/lib/api-client')
const mockHandleResponse = vi.mocked(handleResponse)
const mockMutationHeaders = vi.mocked(mutationHeaders)
const mockRequestHeaders = vi.mocked(requestHeaders)
const mockFetch = vi.fn()

beforeEach(() => {
  mockFetch.mockReset()
  mockHandleResponse.mockReset()
  mockMutationHeaders.mockReset()
  mockRequestHeaders.mockReset()
  mockMutationHeaders.mockResolvedValue({ 'X-CSRF-Token': 'test-token' })
  mockRequestHeaders.mockResolvedValue({ Authorization: 'Bearer test' })
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
  total_frames: 100,
  processed_frames: 100,
} as EpisodeDetectionSummary

describe('runDetection', () => {
  it('POSTs detection request to the episode endpoint', async () => {
    const request: DetectionRequest = { confidence: 0.5, model: 'yolo11n' }
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockResolvedValueOnce(summary)

    const result = await runDetection('ds-1', 7, request)

    expect(result).toBe(summary)
    expect(mockMutationHeaders).toHaveBeenCalledTimes(1)
    const [url, init] = mockFetch.mock.calls[0]
    expect(url).toBe('/api/datasets/ds-1/episodes/7/detect')
    expect(init).toMatchObject({
      method: 'POST',
      body: JSON.stringify(request),
    })
    expect(init.headers).toMatchObject({
      'Content-Type': 'application/json',
      'X-CSRF-Token': 'test-token',
    })
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
})

describe('clearDetections', () => {
  it('DELETEs the detections endpoint with mutation headers', async () => {
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockResolvedValueOnce({ cleared: true })

    const result = await clearDetections('ds-1', 9)

    expect(result).toEqual({ cleared: true })
    expect(mockMutationHeaders).toHaveBeenCalledTimes(1)
    expect(mockFetch).toHaveBeenCalledWith('/api/datasets/ds-1/episodes/9/detections', {
      method: 'DELETE',
      headers: { 'X-CSRF-Token': 'test-token' },
    })
  })

  it('propagates errors from handleResponse', async () => {
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockRejectedValueOnce(new Error('forbidden'))

    await expect(clearDetections('ds-1', 9)).rejects.toThrow('forbidden')
  })
})
