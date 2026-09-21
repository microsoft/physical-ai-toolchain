import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { _resetCsrfToken } from '@/lib/api-client'
import { jsonResponse } from '@/test-utils/fetch-mocks'
import type { ExportProgress, ExportResult } from '@/types'

import { createExportStream, exportEpisodes, type ExportRequestWithEdits } from '../export'

vi.mock('@/lib/auth-headers', () => ({
  getAuthHeaders: async () => ({}),
}))

const request: ExportRequestWithEdits = {
  episodeIndices: [0],
  outputPath: '/tmp/export',
  applyEdits: false,
  includeSubtasks: false,
  format: 'hdf5',
}

const wireResult = {
  success: true,
  outputFiles: ['episode_0.hdf5'],
  error: null,
  stats: { total_episodes: 1, total_frames: 10, removed_frames: 0, duration_ms: 25 },
}

function serve(response: Response): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) =>
      url === '/api/csrf-token' ? jsonResponse({ csrf_token: 'test-csrf' }) : response,
    ),
  )
}

function stream(chunks: string[]): Response {
  const encoder = new TextEncoder()
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
        controller.close()
      },
    }),
    { headers: { 'Content-Type': 'text/event-stream' } },
  )
}

async function consume(chunks: string[]) {
  serve(stream(chunks))
  const progress = vi.fn<(value: ExportProgress) => void>()
  const complete = vi.fn<(value: ExportResult) => void>()
  const error = vi.fn<(value: string) => void>()
  createExportStream('ds-1', request, progress, complete, error)
  await vi.waitFor(() => expect(complete.mock.calls.length + error.mock.calls.length).toBe(1))
  return { progress, complete, error }
}

beforeEach(() => {
  _resetCsrfToken()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('export wire contract', () => {
  it('normalizes real batch statistics and preserves null success errors', async () => {
    const { complete, error } = await consume([
      'event: complete\n',
      `data: ${JSON.stringify(wireResult)}\n\n`,
    ])
    expect(error).not.toHaveBeenCalled()
    expect(complete).toHaveBeenCalledWith({
      ...wireResult,
      stats: { totalEpisodes: 1, totalFrames: 10, removedFrames: 0, durationMs: 25 },
    })
  })

  it('preserves failed batch results without forwarding diagnostic errors', async () => {
    const { complete, error } = await consume([
      `event: complete\ndata: ${JSON.stringify({
        ...wireResult,
        success: false,
        error: '/srv/data/private: permission denied',
      })}\n\n`,
    ])
    expect(error).not.toHaveBeenCalled()
    expect(complete).toHaveBeenCalledWith({
      success: false,
      outputFiles: ['episode_0.hdf5'],
      error: 'Export failed',
      stats: { totalEpisodes: 1, totalFrames: 10, removedFrames: 0, durationMs: 25 },
    })
  })

  it('applies the same public result boundary to synchronous export', async () => {
    serve(jsonResponse({ ...wireResult, success: false, error: '/srv/data/private: denied' }))
    await expect(exportEpisodes('ds-1', request)).resolves.toMatchObject({
      success: false,
      error: 'Export failed',
      stats: { totalEpisodes: 1 },
    })
  })

  it.each(
    [
      null,
      [],
      'invalid',
      { ...wireResult, stats: {} },
      { ...wireResult, success: false, stats: {} },
      { ...wireResult, outputFiles: [1] },
    ].map((payload) => ({ payload })),
  )('rejects malformed completion payload $payload with a public error', async ({ payload }) => {
    const { complete, error } = await consume([
      `event: complete\ndata: ${JSON.stringify(payload)}\n\n`,
    ])
    expect(complete).not.toHaveBeenCalled()
    expect(error).toHaveBeenCalledWith('Export failed')
  })

  it.each(['total_episodes', 'total_frames', 'removed_frames', 'duration_ms'])(
    'validates the %s statistic',
    async (field) => {
      const { complete, error } = await consume([
        `event: complete\ndata: ${JSON.stringify({
          ...wireResult,
          stats: { ...wireResult.stats, [field]: 'invalid' },
        })}\n\n`,
      ])
      expect(complete).not.toHaveBeenCalled()
      expect(error).toHaveBeenCalledWith('Export failed')
    },
  )

  it('continues after a non-object progress payload', async () => {
    const { complete, error } = await consume([
      `event: progress\ndata: null\n\nevent: complete\ndata: ${JSON.stringify(wireResult)}\n\n`,
    ])
    expect(complete).toHaveBeenCalledOnce()
    expect(error).not.toHaveBeenCalled()
  })

  it.each(['', 'event: complete\ndata: {"success":'])(
    'reports a stream ending without a valid terminal event',
    async (chunk) => {
      const { complete, error } = await consume([chunk])
      expect(complete).not.toHaveBeenCalled()
      expect(error).toHaveBeenCalledWith('Export failed')
    },
  )
})
