import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { OperatorStatus, PreflightResult } from '@/api/operator'
import {
  fetchOperatorCameraFrame,
  parseOperatorEventBlock,
  sendOperatorCommand,
  startOperatorSession,
  streamOperatorEvents,
} from '@/api/operator'

vi.mock('@/lib/api-client', () => ({
  handleResponse: vi.fn(async () => ({
    service_instance_id: 'service-1',
    revision: 2,
    state: 'running',
    session_id: 'session-1',
  })),
  mutationHeaders: vi.fn(async () => ({ 'X-CSRF-Token': 'csrf' })),
  requestHeaders: vi.fn(async () => ({ Authorization: 'Bearer token' })),
  transformKeys: vi.fn((value) => value),
}))

const mockFetch = vi.fn()
const { requestHeaders } = await import('@/lib/api-client')

beforeEach(() => {
  mockFetch.mockReset()
  mockFetch.mockResolvedValue({ ok: true } as Response)
  vi.stubGlobal('fetch', mockFetch)
})

describe('startOperatorSession', () => {
  it('includes the stable command ID in the request body', async () => {
    await startOperatorSession('record', 'start-command-1')

    expect(mockFetch).toHaveBeenCalledWith('/api/operator/sessions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': 'csrf',
      },
      body: JSON.stringify({ command_id: 'start-command-1', mode: 'record' }),
    })
  })

  it('includes preflight evidence for LeRobot start', async () => {
    const preflight = {
      preflightId: 'preflight-1',
      profile: 'so101',
      resourceFingerprint: 'resource',
    } as PreflightResult

    await startOperatorSession('teleoperate', 'hardware-start', preflight)

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/operator/sessions',
      expect.objectContaining({
        body: JSON.stringify({
          command_id: 'hardware-start',
          mode: 'teleoperate',
          profile: 'so101',
          preflight_id: 'preflight-1',
          preflight_fingerprint: 'resource',
        }),
      }),
    )
  })
})

describe('fetchOperatorCameraFrame', () => {
  it('fetches an authenticated no-store JPEG blob', async () => {
    const blob = new Blob(['jpeg'], { type: 'image/jpeg' })
    const abortController = new AbortController()
    mockFetch.mockResolvedValueOnce({ ok: true, blob: async () => blob } as Response)

    await expect(fetchOperatorCameraFrame('wrist', abortController.signal)).resolves.toBe(blob)

    expect(mockFetch).toHaveBeenCalledWith('/api/operator/cameras/wrist/frame', {
      headers: { Authorization: 'Bearer token' },
      signal: abortController.signal,
      cache: 'no-store',
    })
  })
})

describe('sendOperatorCommand', () => {
  it('does not couple commands to telemetry-driven status revisions', async () => {
    const status = {
      serviceInstanceId: 'service-1',
      revision: 42,
      state: 'running',
      sessionId: 'session-1',
      mode: 'record',
      workerPid: 12,
      lastCommand: null,
      cleanupUnconfirmed: false,
      error: null,
    } satisfies OperatorStatus

    await sendOperatorCommand(status, 'pause', 'pause-command')

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/operator/sessions/session-1/commands',
      expect.objectContaining({
        body: JSON.stringify({
          command_id: 'pause-command',
          action: 'pause',
        }),
      }),
    )
  })
})

describe('parseOperatorEventBlock', () => {
  it('parses a revision-bound status event', () => {
    const status: OperatorStatus = {
      serviceInstanceId: 'service-1',
      revision: 4,
      state: 'running',
      sessionId: 'session-1',
      mode: 'record',
      workerPid: 42,
      lastCommand: null,
      cleanupUnconfirmed: false,
      error: null,
    }
    const block = ['id: service-1:4', 'event: status', `data: ${JSON.stringify(status)}`].join('\n')

    expect(parseOperatorEventBlock(block)).toEqual({
      eventId: 'service-1:4',
      status,
    })
  })

  it('ignores heartbeats and malformed events', () => {
    expect(parseOperatorEventBlock('event: heartbeat\ndata: {}')).toBeNull()
    expect(parseOperatorEventBlock('event: status\ndata: not-json')).toBeNull()
  })
})

describe('streamOperatorEvents', () => {
  it('sends auth and replay headers and parses streamed chunks', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'id: service-1:2\nevent: status\ndata: {"service_instance_id":"service-1","revision":2,"state":"idle","session_id":"","mode":null,"worker_pid":null,"last_command":null,"cleanup_unconfirmed":false,"error":null}\n\n',
          ),
        )
        controller.close()
      },
    })
    mockFetch.mockResolvedValueOnce({ ok: true, body: stream } as Response)
    const onEvent = vi.fn()
    const abortController = new AbortController()

    await streamOperatorEvents('service-1:1', onEvent, abortController.signal)

    expect(requestHeaders).toHaveBeenCalled()
    expect(mockFetch).toHaveBeenCalledWith('/api/operator/events', {
      headers: { Authorization: 'Bearer token', 'Last-Event-ID': 'service-1:1' },
      signal: abortController.signal,
    })
    expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({ eventId: 'service-1:2' }))
  })

  it('stops reading when aborted', async () => {
    const abortController = new AbortController()
    abortController.abort()
    mockFetch.mockRejectedValueOnce(new DOMException('Aborted', 'AbortError'))

    await expect(streamOperatorEvents(null, vi.fn(), abortController.signal)).rejects.toThrow(
      'Aborted',
    )
  })
})
