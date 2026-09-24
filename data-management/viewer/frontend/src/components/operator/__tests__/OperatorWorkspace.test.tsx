import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { OperatorWorkspace } from '@/components/operator/OperatorWorkspace'
import type { OperatorController } from '@/hooks/use-operator'

function createOperator(overrides: Partial<OperatorController> = {}): OperatorController {
  return {
    capabilities: {
      enabled: true,
      adapterMode: 'lerobot',
      adapterVersion: 1,
      protocolVersion: 2,
      modes: ['teleoperate', 'record', 'policy'],
      profiles: ['so101'],
      robots: [
        {
          role: 'leader',
          name: '/dev/serial/by-id/private-leader',
          embodiment: 'SO-101',
          actuatorCount: 6,
        },
        {
          role: 'follower',
          name: 'private-serial-1234',
          embodiment: 'SO-101',
          actuatorCount: 6,
        },
      ],
      cameras: [{ name: 'wrist', defaultFps: 30 }],
      preflightEnabled: true,
      sessionStartEnabled: true,
      reason: null,
    },
    status: {
      serviceInstanceId: 'service-1',
      revision: 1,
      state: 'idle',
      sessionId: '',
      mode: null,
      workerPid: null,
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
    },
    calibration: undefined,
    preflight: undefined,
    telemetry: [],
    connectionState: 'connected',
    isLoading: false,
    isPending: false,
    error: null,
    calibrationError: null,
    checkCalibration: vi.fn(),
    runPreflight: vi.fn(),
    startSession: vi.fn(),
    sendCommand: vi.fn(),
    stopSession: vi.fn(),
    ...overrides,
  }
}

describe('OperatorWorkspace', () => {
  it('renders explicit loading and unavailable states', () => {
    const { rerender } = render(
      <OperatorWorkspace operator={createOperator({ isLoading: true })} />,
    )

    expect(screen.getByRole('status')).toHaveTextContent('Loading operator workspace')

    rerender(
      <OperatorWorkspace
        operator={createOperator({
          capabilities: {
            ...createOperator().capabilities!,
            enabled: false,
            adapterMode: 'disabled',
            reason: 'Operator access is not configured',
          },
          connectionState: 'disabled',
        })}
      />,
    )

    expect(screen.getByText('Operator unavailable')).toBeInTheDocument()
    expect(screen.getByText('Operator access is not configured')).toBeInTheDocument()
  })

  it('gates start on saved calibration and current preflight', async () => {
    const operator = createOperator()
    const user = userEvent.setup()
    const { rerender } = render(<OperatorWorkspace operator={operator} />)

    expect(screen.getByRole('button', { name: 'Start teleoperation' })).toBeDisabled()
    expect(screen.getByText(/saved-file validation has not run/i)).toBeInTheDocument()

    operator.calibration = {
      profile: 'so101',
      checkedAt: '2026-09-24T12:00:00Z',
      valid: true,
      hardwareVerified: false,
      arms: [],
    }
    operator.preflight = {
      preflightId: 'preflight-1',
      lifecycle: 'completed',
      profile: 'so101',
      mode: 'teleoperate',
      profileFingerprint: 'profile-fingerprint',
      resourceFingerprint: 'resource-fingerprint',
      createdAt: '2026-09-24T12:00:00Z',
      expiresAt: '2099-09-24T12:00:00Z',
      checks: [],
      ownershipComplete: true,
      startEligible: true,
    }
    rerender(<OperatorWorkspace operator={operator} />)

    await user.click(screen.getByRole('button', { name: 'Start teleoperation' }))
    expect(operator.startSession).toHaveBeenCalledWith(
      expect.objectContaining({
        mode: 'teleoperate',
        profile: 'so101',
        preflightId: 'preflight-1',
        preflightFingerprint: 'resource-fingerprint',
      }),
    )
  })

  it('keeps stop reachable while a command is pending or cleanup is uncertain', async () => {
    const operator = createOperator({
      isPending: true,
      status: {
        ...createOperator().status!,
        state: 'stopping',
        sessionId: 'session-1',
        mode: 'record',
        cleanupUnconfirmed: true,
      },
    })
    const user = userEvent.setup()
    render(<OperatorWorkspace operator={operator} />)

    expect(screen.getByText('Cleanup unconfirmed')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Stop session' }))
    expect(operator.stopSession).toHaveBeenCalledOnce()
  })

  it('shows bounded controls, empty monitoring states, and no device identifiers', async () => {
    const user = userEvent.setup()
    render(<OperatorWorkspace operator={createOperator()} />)

    expect(screen.getByLabelText('Control FPS')).toHaveAttribute('max', '120')
    await user.click(screen.getByRole('tab', { name: 'Record' }))
    expect(screen.getByLabelText('Episode seconds')).toHaveAttribute('max', '3600')
    expect(screen.getByText('Preview starts when the worker owns the camera')).toBeInTheDocument()
    expect(screen.getByText('Telemetry appears while a session is running')).toBeInTheDocument()
    expect(screen.getByText('Trajectory appears while a session is running')).toBeInTheDocument()
    expect(screen.queryByText(/private-leader|private-serial/i)).not.toBeInTheDocument()
  })

  it('labels simulation separately from physical operation', () => {
    render(
      <OperatorWorkspace
        operator={createOperator({
          capabilities: {
            ...createOperator().capabilities!,
            adapterMode: 'simulated',
            preflightEnabled: false,
          },
        })}
      />,
    )

    expect(screen.getByText('Simulation')).toBeInTheDocument()
    expect(screen.getByText(/does not move physical hardware/i)).toBeInTheDocument()
  })

  it('starts simulation without requiring a physical profile or preflight', async () => {
    const operator = createOperator({
      capabilities: {
        ...createOperator().capabilities!,
        adapterMode: 'simulated',
        profiles: [],
        preflightEnabled: false,
      },
    })
    const user = userEvent.setup()
    render(<OperatorWorkspace operator={operator} />)

    await user.click(screen.getByRole('button', { name: 'Start teleoperation' }))

    expect(operator.startSession).toHaveBeenCalledWith(
      expect.objectContaining({ mode: 'teleoperate', profile: null }),
    )
  })
})
