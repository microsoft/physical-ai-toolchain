import { act, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  MAX_OPERATOR_TELEMETRY_SAMPLES,
  type OperatorEventState,
  operatorKeys,
  reconcileOperatorEvent,
  useOperator,
} from '@/hooks/use-operator'
import { createTestQueryClient, renderHookWithProviders } from '@/test-utils/render'
import type { OperatorStatus } from '@/types'

const api = vi.hoisted(() => ({
  createOperatorPreflight: vi.fn(),
  fetchOperatorCalibration: vi.fn(),
  fetchOperatorCapabilities: vi.fn(),
  fetchOperatorStatus: vi.fn(),
  sendOperatorCommand: vi.fn(),
  startOperatorSession: vi.fn(),
  stopOperatorSession: vi.fn(),
  streamOperatorEvents: vi.fn(),
}))

vi.mock('@/api/operator', () => api)

function status(overrides: Partial<OperatorStatus> = {}): OperatorStatus {
  return {
    serviceInstanceId: 'service-1',
    revision: 1,
    state: 'running',
    sessionId: 'session-1',
    mode: 'teleoperate',
    workerPid: 42,
    lastCommand: null,
    cleanupUnconfirmed: false,
    error: null,
    targetHz: null,
    actualHz: null,
    loopP95Ms: null,
    loopMaxMs: null,
    overruns: 0,
    latestWorkerLog: null,
    latestTelemetry: null,
    sessionSettings: null,
    datasetId: null,
    episodeIndex: 0,
    recordingPhase: null,
    uploadStatus: 'not_requested',
    uploadError: null,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  api.fetchOperatorCapabilities.mockResolvedValue({
    enabled: true,
    adapterMode: 'simulated',
    adapterVersion: 1,
    protocolVersion: 1,
    modes: ['teleoperate'],
    profiles: ['so101'],
    robots: [],
    cameras: [],
    preflightEnabled: true,
    sessionStartEnabled: true,
    reason: null,
  })
  api.fetchOperatorStatus.mockResolvedValue(status())
  api.streamOperatorEvents.mockImplementation(
    (_lastEventId: string | null, _onEvent: unknown, signal: AbortSignal) =>
      new Promise<void>((resolve) => signal.addEventListener('abort', () => resolve())),
  )
})

describe('operator event reconciliation', () => {
  it('rejects stale service, session, process, and revision identities', () => {
    const current = status({ revision: 5 })

    expect(reconcileOperatorEvent(current, status({ revision: 4 }))).toBe(current)
    expect(
      reconcileOperatorEvent(current, status({ serviceInstanceId: 'service-old', revision: 6 })),
    ).toBe(current)
    expect(reconcileOperatorEvent(current, status({ sessionId: 'session-old', revision: 6 }))).toBe(
      current,
    )
    expect(reconcileOperatorEvent(current, status({ workerPid: 99, revision: 6 }))).toBe(current)
    expect(reconcileOperatorEvent(current, status({ revision: 6 })).revision).toBe(6)
  })

  it('accepts a newer session after an idle or terminal authoritative state', () => {
    const idle = status({ revision: 5, state: 'idle', sessionId: '', mode: null, workerPid: null })
    const completed = status({ revision: 8, state: 'completed' })

    expect(
      reconcileOperatorEvent(idle, status({ revision: 6, sessionId: 'session-2' })),
    ).toMatchObject({
      revision: 6,
      sessionId: 'session-2',
    })
    expect(
      reconcileOperatorEvent(completed, status({ revision: 9, sessionId: 'session-2' })),
    ).toMatchObject({ revision: 9, sessionId: 'session-2' })
  })

  it('bounds and deduplicates telemetry without retaining command state', () => {
    let eventState: OperatorEventState = { telemetry: [], lastEventId: null }
    for (let index = 0; index <= MAX_OPERATOR_TELEMETRY_SAMPLES; index += 1) {
      eventState = reconcileOperatorEvent(
        status({ revision: index, latestTelemetry: null }),
        status({
          revision: index + 1,
          latestTelemetry: {
            elapsedS: index,
            leader: {},
            follower: {},
            commanded: {},
          },
        }),
        eventState,
        `service-1:${index + 1}`,
      ).eventState
    }

    expect(eventState.telemetry).toHaveLength(MAX_OPERATOR_TELEMETRY_SAMPLES)
    expect(eventState.telemetry[0]?.elapsedS).toBe(1)
    expect(Object.keys(eventState)).toEqual(['telemetry', 'lastEventId'])
  })
})

describe('useOperator', () => {
  it('stores authoritative status in TanStack Query and keeps one command id across retry', async () => {
    const queryClient = createTestQueryClient()
    api.fetchOperatorStatus.mockResolvedValueOnce(
      status({ revision: 0, state: 'idle', sessionId: '', mode: null, workerPid: null }),
    )
    api.startOperatorSession
      .mockRejectedValueOnce(new TypeError('temporary network failure'))
      .mockResolvedValueOnce(status({ revision: 2 }))

    const { result } = renderHookWithProviders(() => useOperator(), { queryClient })
    await waitFor(() => expect(result.current.status?.revision).toBe(0))

    act(() => result.current.startSession({ mode: 'teleoperate' }))
    await waitFor(() => expect(api.startOperatorSession).toHaveBeenCalledTimes(2))

    const firstRequest = api.startOperatorSession.mock.calls[0][0]
    const retryRequest = api.startOperatorSession.mock.calls[1][0]
    expect(retryRequest.commandId).toBe(firstRequest.commandId)
    expect(queryClient.getQueryData(operatorKeys.status())).toMatchObject({ revision: 2 })
  })

  it('reconciles authoritative status before reconnecting and never replays a command', async () => {
    let streamCalls = 0
    api.streamOperatorEvents.mockImplementation(
      async (_lastEventId: string | null, onEvent: (event: unknown) => void) => {
        streamCalls += 1
        if (streamCalls === 1) {
          onEvent({ eventId: 'service-1:2', status: status({ revision: 2 }) })
          throw new TypeError('disconnected')
        }
        return new Promise<void>(() => undefined)
      },
    )
    api.fetchOperatorStatus
      .mockResolvedValueOnce(status())
      .mockResolvedValueOnce(
        status({ serviceInstanceId: 'service-2', sessionId: '', workerPid: null, revision: 0 }),
      )

    const queryClient = createTestQueryClient()
    const { result } = renderHookWithProviders(() => useOperator({ reconnectDelayMs: 0 }), {
      queryClient,
    })

    await waitFor(() => expect(streamCalls).toBe(2))
    expect(api.fetchOperatorStatus).toHaveBeenCalledTimes(2)
    expect(queryClient.getQueryData(operatorKeys.status())).toMatchObject({
      serviceInstanceId: 'service-2',
      revision: 0,
    })
    expect(result.current.connectionState).toBe('connecting')
    expect(api.sendOperatorCommand).not.toHaveBeenCalled()
    expect(api.startOperatorSession).not.toHaveBeenCalled()
  })

  it('marks the event stream connected when its snapshot matches the status query', async () => {
    api.streamOperatorEvents.mockImplementation(
      async (_lastEventId: string | null, onEvent: (event: unknown) => void) => {
        onEvent({ eventId: 'service-1:1', status: status() })
        return new Promise<void>(() => undefined)
      },
    )

    const { result } = renderHookWithProviders(() => useOperator())

    await waitFor(() => expect(result.current.connectionState).toBe('connected'))
  })
})
