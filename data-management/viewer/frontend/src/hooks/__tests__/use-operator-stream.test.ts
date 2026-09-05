import { act, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useOperator } from '@/hooks/use-operator'
import { renderHookWithProviders } from '@/test-utils/render'

const mocks = vi.hoisted(() => ({
  fetchCapabilities: vi.fn(),
  fetchStatus: vi.fn(),
  streamEvents: vi.fn(),
  startSession: vi.fn(),
  sendCommand: vi.fn(),
  stopSession: vi.fn(),
  createPreflight: vi.fn(),
}))

vi.mock('@/api/operator', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/api/operator')>()
  return {
    ...original,
    fetchOperatorCapabilities: mocks.fetchCapabilities,
    fetchOperatorStatus: mocks.fetchStatus,
    streamOperatorEvents: mocks.streamEvents,
    startOperatorSession: mocks.startSession,
    sendOperatorCommand: mocks.sendCommand,
    stopOperatorSession: mocks.stopSession,
    createOperatorPreflight: mocks.createPreflight,
  }
})

vi.mock('@/lib/playback-diagnostics', () => ({
  recordDiagnosticEvent: vi.fn(),
}))

beforeEach(() => {
  mocks.fetchCapabilities.mockReset().mockResolvedValue({
    enabled: true,
    adapterMode: 'simulated',
    adapterVersion: 1,
    protocolVersion: 1,
    modes: ['teleoperate', 'record'],
    profiles: [],
    reason: null,
  })
  mocks.fetchStatus.mockReset().mockResolvedValue({
    serviceInstanceId: 'service-1',
    revision: 0,
    state: 'idle',
    sessionId: '',
    mode: null,
    workerPid: null,
    lastCommand: null,
    cleanupUnconfirmed: false,
    error: null,
  })
  mocks.streamEvents.mockReset().mockRejectedValue(new Error('stream disconnected'))
  mocks.startSession.mockReset().mockRejectedValueOnce(new Error('temporary')).mockResolvedValue({
    serviceInstanceId: 'service-1',
    revision: 2,
    state: 'running',
    sessionId: 'session-1',
    mode: 'teleoperate',
    workerPid: 42,
    lastCommand: null,
    cleanupUnconfirmed: false,
    error: null,
  })
  mocks.sendCommand.mockReset().mockRejectedValueOnce(new Error('temporary')).mockResolvedValue({
    serviceInstanceId: 'service-1',
    revision: 3,
    state: 'running',
    sessionId: 'session-1',
    mode: 'record',
    workerPid: 42,
    lastCommand: 'save',
    cleanupUnconfirmed: false,
    error: null,
  })
  mocks.stopSession.mockReset().mockRejectedValueOnce(new Error('temporary')).mockResolvedValue({
    serviceInstanceId: 'service-1',
    revision: 4,
    state: 'cancelled',
    sessionId: 'session-1',
    mode: 'record',
    workerPid: 42,
    lastCommand: 'cancel',
    cleanupUnconfirmed: false,
    error: null,
  })
  mocks.createPreflight.mockReset().mockResolvedValue({
    preflightId: 'preflight-1',
    lifecycle: 'completed',
    profile: 'so101',
    mode: 'teleoperate',
    profileFingerprint: 'profile',
    resourceFingerprint: 'resource',
    createdAt: '2026-07-22T00:00:00Z',
    expiresAt: '2099-07-22T00:00:30Z',
    checks: [],
    ownershipComplete: true,
    startEligible: true,
  })
})

describe('useOperator event recovery', () => {
  it('exposes retrying state and refreshes the status snapshot after disconnect', async () => {
    const { result } = renderHookWithProviders(() => useOperator())

    await waitFor(() => expect(result.current.connectionState).toBe('retrying'))
    await waitFor(() => expect(mocks.fetchStatus.mock.calls.length).toBeGreaterThan(1))

    expect(result.current.error).toBe('stream disconnected')
  })

  it('reuses one start command ID across mutation retries', async () => {
    const { result } = renderHookWithProviders(() => useOperator())
    await waitFor(() => expect(result.current.status?.state).toBe('idle'))

    act(() => result.current.startSession('teleoperate'))

    await waitFor(() => expect(mocks.startSession).toHaveBeenCalledTimes(2))
    expect(mocks.startSession.mock.calls[0][1]).toBe(mocks.startSession.mock.calls[1][1])
  })

  it('clears consumed preflight evidence after successful start', async () => {
    const { result } = renderHookWithProviders(() => useOperator())
    await waitFor(() => expect(result.current.status?.state).toBe('idle'))

    act(() => result.current.runPreflight('teleoperate'))
    await waitFor(() => expect(result.current.preflight).toBeDefined())
    act(() => result.current.startSession('teleoperate'))
    await waitFor(() => expect(mocks.startSession).toHaveBeenCalled())

    expect(result.current.preflight).toBeUndefined()
  })

  it('reuses one episode command ID across mutation retries', async () => {
    const { result, queryClient } = renderHookWithProviders(() => useOperator())
    await waitFor(() => expect(result.current.status?.state).toBe('idle'))
    queryClient.setQueryData(['operator', 'status'], {
      serviceInstanceId: 'service-1',
      revision: 2,
      state: 'running',
      sessionId: 'session-1',
      mode: 'record',
      workerPid: 42,
      lastCommand: null,
      cleanupUnconfirmed: false,
      error: null,
    })

    act(() => result.current.sendCommand('save'))

    await waitFor(() => expect(mocks.sendCommand).toHaveBeenCalledTimes(2))
    expect(mocks.sendCommand.mock.calls[0][2]).toBe(mocks.sendCommand.mock.calls[1][2])
  })

  it('reuses one stop command ID across mutation retries', async () => {
    const { result, queryClient } = renderHookWithProviders(() => useOperator())
    await waitFor(() => expect(result.current.status?.state).toBe('idle'))
    queryClient.setQueryData(['operator', 'status'], {
      serviceInstanceId: 'service-1',
      revision: 2,
      state: 'running',
      sessionId: 'session-1',
      mode: 'record',
      workerPid: 42,
      lastCommand: null,
      cleanupUnconfirmed: false,
      error: null,
    })

    act(() => result.current.stopSession())

    await waitFor(() => expect(mocks.stopSession).toHaveBeenCalledTimes(2))
    expect(mocks.stopSession.mock.calls[0][1]).toBe(mocks.stopSession.mock.calls[1][1])
  })

  it('treats a clean stream EOF as retrying and refreshes status', async () => {
    mocks.streamEvents.mockReset().mockResolvedValue(undefined)
    const { result } = renderHookWithProviders(() => useOperator())

    await waitFor(() => expect(result.current.connectionState).toBe('retrying'))
    await waitFor(() => expect(mocks.fetchStatus.mock.calls.length).toBeGreaterThan(1))
  })
})
