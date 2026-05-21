import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ExportProgress, ExportResult } from '@/types'

import type { ExportPreviewStats, ExportRequestWithEdits } from '../export'
import { createExportStream, exportEpisodes, getExportPreview } from '../export'

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

const baseRequest: ExportRequestWithEdits = {
  episodeIndices: [0, 1, 2],
  outputPath: '/tmp/out',
  applyEdits: true,
  includeSubtasks: false,
  format: 'parquet',
}

describe('exportEpisodes', () => {
  it('POSTs the export request with mutation headers', async () => {
    const result: ExportResult = {
      success: true,
      outputFiles: ['out.parquet'],
      stats: { totalEpisodes: 3, totalFrames: 300, removedFrames: 0, durationMs: 100 },
    }
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockResolvedValueOnce(result)

    const response = await exportEpisodes('ds-1', baseRequest)

    expect(response).toEqual(result)
    expect(mockMutationHeaders).toHaveBeenCalledTimes(1)
    const [url, init] = mockFetch.mock.calls[0]
    expect(url).toBe('/api/datasets/ds-1/export')
    expect(init).toMatchObject({
      method: 'POST',
      body: JSON.stringify(baseRequest),
    })
    expect(init.headers).toMatchObject({
      'Content-Type': 'application/json',
      'X-CSRF-Token': 'test-token',
    })
  })
})

describe('getExportPreview', () => {
  const stats: ExportPreviewStats = {
    totalEpisodes: 3,
    totalFrames: 300,
    estimatedOutputSize: '12 MB',
    removedFramesCount: 0,
  }

  it('GETs the preview endpoint with comma-joined episode indices', async () => {
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockResolvedValueOnce(stats)

    const result = await getExportPreview('ds-1', [0, 1, 2])

    expect(result).toEqual(stats)
    expect(mockRequestHeaders).toHaveBeenCalledTimes(1)
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/datasets/ds-1/export/preview?episode_indices=0%2C1%2C2',
      {
        headers: { Authorization: 'Bearer test' },
      },
    )
  })

  it('appends removed_frames when provided', async () => {
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockResolvedValueOnce(stats)

    await getExportPreview('ds-1', [0], [5, 6])

    const [url] = mockFetch.mock.calls[0]
    expect(url).toContain('episode_indices=0')
    expect(url).toContain('removed_frames=5%2C6')
  })

  it('omits removed_frames when the list is empty', async () => {
    mockFetch.mockResolvedValueOnce(okResponse())
    mockHandleResponse.mockResolvedValueOnce(stats)

    await getExportPreview('ds-1', [0], [])

    const [url] = mockFetch.mock.calls[0]
    expect(url).not.toContain('removed_frames')
  })
})

describe('createExportStream', () => {
  function streamResponse(chunks: string[]): Response {
    const encoder = new TextEncoder()
    const queue = chunks.map((c) => ({ done: false as const, value: encoder.encode(c) }))
    let i = 0
    const reader = {
      read: vi.fn().mockImplementation(() => {
        if (i < queue.length) {
          return Promise.resolve(queue[i++])
        }
        return Promise.resolve({ done: true, value: undefined })
      }),
    }
    return {
      ok: true,
      status: 200,
      statusText: 'OK',
      body: { getReader: () => reader },
    } as unknown as Response
  }

  async function flushMicrotasks() {
    for (let i = 0; i < 10; i++) {
      await Promise.resolve()
    }
  }

  it('routes progress events to onProgress', async () => {
    const progress: ExportProgress = {
      currentEpisode: 1,
      totalEpisodes: 3,
      currentFrame: 50,
      totalFrames: 300,
      percentage: 50,
      status: 'processing',
    }
    mockFetch.mockResolvedValueOnce(
      streamResponse([`event: progress\ndata: ${JSON.stringify(progress)}\n\n`]),
    )
    const onProgress = vi.fn()
    const onComplete = vi.fn()
    const onError = vi.fn()

    createExportStream('ds-1', baseRequest, onProgress, onComplete, onError)
    await flushMicrotasks()

    expect(onProgress).toHaveBeenCalledWith(progress)
    expect(onComplete).not.toHaveBeenCalled()
    expect(onError).not.toHaveBeenCalled()
  })

  it('routes completion payloads to onComplete', async () => {
    const result: ExportResult = {
      success: true,
      outputFiles: ['out.parquet'],
      stats: { totalEpisodes: 3, totalFrames: 300, removedFrames: 0, durationMs: 100 },
    }
    mockFetch.mockResolvedValueOnce(
      streamResponse([`event: complete\ndata: ${JSON.stringify(result)}\n\n`]),
    )
    const onProgress = vi.fn()
    const onComplete = vi.fn()
    const onError = vi.fn()

    createExportStream('ds-1', baseRequest, onProgress, onComplete, onError)
    await flushMicrotasks()

    expect(onComplete).toHaveBeenCalledWith(result)
    expect(onProgress).not.toHaveBeenCalled()
  })

  it('routes error events to onError using the message field', async () => {
    mockFetch.mockResolvedValueOnce(
      streamResponse([`event: error\ndata: ${JSON.stringify({ message: 'boom' })}\n\n`]),
    )
    const onError = vi.fn()

    createExportStream('ds-1', baseRequest, vi.fn(), vi.fn(), onError)
    await flushMicrotasks()

    expect(onError).toHaveBeenCalledWith('boom')
  })

  it('falls back to a default error message when none is provided', async () => {
    mockFetch.mockResolvedValueOnce(streamResponse([`event: error\ndata: {}\n\n`]))
    const onError = vi.fn()

    createExportStream('ds-1', baseRequest, vi.fn(), vi.fn(), onError)
    await flushMicrotasks()

    expect(onError).toHaveBeenCalledWith('Export failed')
  })

  it('ignores malformed JSON data lines', async () => {
    mockFetch.mockResolvedValueOnce(streamResponse([`data: not-json\n\n`]))
    const onProgress = vi.fn()
    const onComplete = vi.fn()
    const onError = vi.fn()

    createExportStream('ds-1', baseRequest, onProgress, onComplete, onError)
    await flushMicrotasks()

    expect(onProgress).not.toHaveBeenCalled()
    expect(onComplete).not.toHaveBeenCalled()
    expect(onError).not.toHaveBeenCalled()
  })

  it('reports HTTP errors via onError', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: 'Server Error',
      body: null,
    } as Response)
    const onError = vi.fn()

    createExportStream('ds-1', baseRequest, vi.fn(), vi.fn(), onError)
    await flushMicrotasks()

    expect(onError).toHaveBeenCalledWith('Export failed: Server Error')
  })

  it('reports missing response body via onError', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      statusText: 'OK',
      body: null,
    } as Response)
    const onError = vi.fn()

    createExportStream('ds-1', baseRequest, vi.fn(), vi.fn(), onError)
    await flushMicrotasks()

    expect(onError).toHaveBeenCalledWith('No response body')
  })

  it('reports non-Error throws via onError with a default message', async () => {
    mockFetch.mockRejectedValueOnce('string failure')
    const onError = vi.fn()

    createExportStream('ds-1', baseRequest, vi.fn(), vi.fn(), onError)
    await flushMicrotasks()

    expect(onError).toHaveBeenCalledWith('Export failed')
  })

  it('silently swallows AbortError when the cleanup function aborts', async () => {
    let abortSignal: AbortSignal | undefined
    mockFetch.mockImplementationOnce((_url: string, init: RequestInit) => {
      abortSignal = init.signal as AbortSignal
      return new Promise((_resolve, reject) => {
        abortSignal?.addEventListener('abort', () => {
          const err = new Error('aborted')
          err.name = 'AbortError'
          reject(err)
        })
      })
    })
    const onError = vi.fn()

    const cleanup = createExportStream('ds-1', baseRequest, vi.fn(), vi.fn(), onError)
    cleanup()
    await flushMicrotasks()

    expect(onError).not.toHaveBeenCalled()
  })

  it('parses multi-line buffered SSE chunks', async () => {
    const progress: ExportProgress = {
      currentEpisode: 2,
      totalEpisodes: 3,
      currentFrame: 100,
      totalFrames: 300,
      percentage: 33,
      status: 'processing',
    }
    mockFetch.mockResolvedValueOnce(
      streamResponse([
        `event: progress\ndata: ${JSON.stringify(progress)}`,
        `\n\nevent: progress\ndata: ${JSON.stringify({ ...progress, percentage: 66 })}\n\n`,
      ]),
    )
    const onProgress = vi.fn()

    createExportStream('ds-1', baseRequest, onProgress, vi.fn(), vi.fn())
    await flushMicrotasks()

    expect(onProgress).toHaveBeenCalledTimes(2)
    expect(onProgress.mock.calls[0][0].percentage).toBe(33)
    expect(onProgress.mock.calls[1][0].percentage).toBe(66)
  })
})
