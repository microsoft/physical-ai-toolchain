import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { OperatorWorkspace } from '@/components/operator/OperatorWorkspace'
import type { OperatorController } from '@/hooks/use-operator'

function createOperator(overrides: Partial<OperatorController> = {}): OperatorController {
  return {
    capabilities: {
      enabled: true,
      adapterMode: 'simulated',
      adapterVersion: 1,
      protocolVersion: 1,
      modes: ['teleoperate', 'record'],
      profiles: [],
      reason: null,
    },
    status: {
      serviceInstanceId: 'service-1',
      revision: 0,
      state: 'idle',
      sessionId: '',
      mode: null,
      workerPid: null,
      lastCommand: null,
      cleanupUnconfirmed: false,
      error: null,
    },
    isLoading: false,
    isPending: false,
    error: null,
    connectionState: 'connected',
    telemetry: [],
    preflight: undefined,
    runPreflight: vi.fn(),
    startSession: vi.fn(),
    sendCommand: vi.fn(),
    stopSession: vi.fn(),
    ...overrides,
  }
}

describe('OperatorWorkspace', () => {
  it('explains when operator mode is disabled', () => {
    render(
      <OperatorWorkspace
        operator={createOperator({
          capabilities: {
            enabled: false,
            adapterMode: 'disabled',
            adapterVersion: 1,
            protocolVersion: 1,
            modes: [],
            profiles: [],
            reason: 'Operator mode is disabled',
          },
          status: {
            ...createOperator().status!,
            state: 'disabled',
          },
        })}
      />,
    )

    expect(screen.getByText('Operator mode is disabled')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /start/i })).not.toBeInTheDocument()
  })

  it('starts either simulated session mode from idle', async () => {
    const operator = createOperator()
    const user = userEvent.setup()
    render(<OperatorWorkspace operator={operator} />)

    await user.click(screen.getByRole('button', { name: 'Start Teleoperation' }))
    await user.click(screen.getByRole('button', { name: 'Start Recording' }))

    expect(operator.startSession).toHaveBeenNthCalledWith(1, 'teleoperate')
    expect(operator.startSession).toHaveBeenNthCalledWith(2, 'record')
  })

  it('does not activate hardware camera previews for simulated sessions', () => {
    render(
      <OperatorWorkspace
        operator={createOperator({
          status: {
            ...createOperator().status!,
            state: 'running',
            sessionId: 'simulated-session',
            mode: 'teleoperate',
            workerPid: 1234,
          },
        })}
      />,
    )

    expect(screen.getByText('wrist camera stopped')).toBeInTheDocument()
    expect(screen.getByText('front camera stopped')).toBeInTheDocument()
  })

  it('shows explicit recording commands for a running record session', async () => {
    const operator = createOperator({
      status: {
        ...createOperator().status!,
        revision: 2,
        state: 'running',
        sessionId: 'record-session',
        mode: 'record',
        workerPid: 1234,
      },
    })
    const user = userEvent.setup()
    render(<OperatorWorkspace operator={operator} />)

    await user.click(screen.getByRole('button', { name: 'Save Episode' }))
    await user.click(screen.getByRole('button', { name: 'Discard Episode' }))
    await user.click(screen.getByRole('button', { name: 'Finish Recording' }))
    await user.click(screen.getByRole('button', { name: 'Discard Recording' }))

    expect(operator.sendCommand).toHaveBeenNthCalledWith(1, 'save')
    expect(operator.sendCommand).toHaveBeenNthCalledWith(2, 'rerecord')
    expect(operator.sendCommand).toHaveBeenNthCalledWith(3, 'finish')
    expect(operator.stopSession).toHaveBeenCalledOnce()
  })

  it('shows record controls for a running LeRobot session', () => {
    render(
      <OperatorWorkspace
        operator={createOperator({
          capabilities: {
            enabled: true,
            adapterMode: 'lerobot',
            adapterVersion: 1,
            protocolVersion: 2,
            modes: ['teleoperate', 'record'],
            profiles: ['so101'],
            robots: [
              { role: 'leader', name: 'my_leader_arm', embodiment: 'SO-101', actuatorCount: 6 },
              { role: 'follower', name: 'my_follower_arm', embodiment: 'SO-101', actuatorCount: 6 },
            ],
            cameras: [
              { name: 'wrist', defaultFps: 30 },
              { name: 'front', defaultFps: 30 },
            ],
            preflightEnabled: true,
            sessionStartEnabled: true,
            reason: null,
          },
          status: {
            ...createOperator().status!,
            state: 'running',
            sessionId: 'record-session',
            mode: 'record',
            workerPid: 1234,
            datasetId: 'so101-demo',
            episodeIndex: 0,
            sessionSettings: {
              controlFps: 30,
              cameraFps: { wrist: 30, front: 30 },
              maxRelativeTarget: null,
              datasetName: 'so101-demo',
              task: 'Pick <obj> from <loc1> and place in <obj2>',
              saveDestination: 'local',
              hubRepoId: null,
              numEpisodes: 50,
              episodeTimeS: 60,
              resetTimeS: 30,
              rolloutTimeS: 30,
            },
          },
        })}
      />,
    )

    expect(screen.getByRole('button', { name: 'Save Episode' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Discard Episode' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Finish Recording' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Pause Recording' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Discard Recording' })).toHaveAttribute(
      'title',
      'Cancel this session and delete every episode recorded in this session',
    )
    expect(screen.getByTestId('episode-progress')).toHaveTextContent('Episode 1 of 50')
    expect(screen.getByText('30 FPS')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Wrist camera' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Front camera' })).toBeInTheDocument()
    expect(screen.getByText('SO-101 leader')).toBeInTheDocument()
    expect(screen.getByText('SO-101 follower')).toBeInTheDocument()
    expect(screen.getByText('6 actuators')).toBeInTheDocument()
  })

  it('resumes a paused recording and keeps episode actions available', async () => {
    const operator = createOperator({
      status: {
        ...createOperator().status!,
        state: 'running',
        sessionId: 'record-session',
        mode: 'record',
        recordingPhase: 'paused',
      },
    })
    const user = userEvent.setup()
    render(<OperatorWorkspace operator={operator} />)

    await user.click(screen.getByRole('button', { name: 'Resume Recording' }))

    expect(operator.sendCommand).toHaveBeenCalledWith('resume')
    expect(screen.getByText(/teleoperation remains active/i)).toBeInTheDocument()
  })

  it('shows errors and allows retry after a confirmed failed cleanup', () => {
    render(
      <OperatorWorkspace
        operator={createOperator({
          status: {
            ...createOperator().status!,
            state: 'failed',
            sessionId: 'failed-session',
            cleanupUnconfirmed: false,
            error: 'Worker exited',
          },
          error: 'Start failed',
        })}
      />,
    )

    expect(screen.getByText('Worker exited')).toBeInTheDocument()
    expect(screen.getByText('Start failed')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start Teleoperation' })).toBeInTheDocument()
  })

  it('keeps cleanup warnings visible and blocks restart', () => {
    render(
      <OperatorWorkspace
        operator={createOperator({
          status: {
            ...createOperator().status!,
            state: 'cancelled',
            sessionId: 'cancelled-session',
            cleanupUnconfirmed: true,
          },
        })}
      />,
    )

    expect(screen.getByText(/cleanup was not confirmed/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start Teleoperation' })).not.toBeInTheDocument()
  })

  it('shows event stream reconnect state', () => {
    render(
      <OperatorWorkspace
        operator={createOperator({
          connectionState: 'retrying',
          error: 'stream disconnected',
        })}
      />,
    )

    expect(screen.getByText('Reconnecting')).toBeInTheDocument()
    expect(screen.getByText('stream disconnected')).toBeInTheDocument()
  })

  it('runs read-only readiness and renders blocking remediation without start controls', async () => {
    const operator = createOperator({
      capabilities: {
        enabled: true,
        adapterMode: 'lerobot',
        adapterVersion: 1,
        protocolVersion: 1,
        modes: [],
        profiles: ['so101'],
        preflightEnabled: true,
        sessionStartEnabled: false,
        reason: 'LeRobot operator adapter is not implemented',
      },
      preflight: {
        preflightId: 'preflight-1',
        lifecycle: 'completed',
        profile: 'so101',
        mode: 'record',
        profileFingerprint: 'profile',
        resourceFingerprint: 'resource',
        createdAt: '2026-07-22T00:00:00Z',
        expiresAt: '2026-07-22T00:00:30Z',
        ownershipComplete: true,
        startEligible: false,
        checks: [
          {
            name: 'front_camera',
            outcome: 'blocking',
            detail: 'Configured serial missing; observed replacement',
            remediation: 'Confirm the replacement camera serial',
          },
        ],
      },
    })
    const user = userEvent.setup()
    render(<OperatorWorkspace operator={operator} />)

    await user.click(screen.getByRole('button', { name: 'Run SO-101 Preflight' }))

    expect(operator.runPreflight).toHaveBeenCalledWith('teleoperate', false)
    expect(screen.getByText('blocking')).toBeInTheDocument()
    expect(screen.getByText(/Configured serial missing/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start Recording' })).not.toBeInTheDocument()
  })

  it('enables only teleoperation after eligible LeRobot preflight', async () => {
    const operator = createOperator({
      capabilities: {
        enabled: true,
        adapterMode: 'lerobot',
        adapterVersion: 1,
        protocolVersion: 1,
        modes: ['teleoperate'],
        profiles: ['so101'],
        preflightEnabled: true,
        sessionStartEnabled: true,
        reason: null,
      },
      preflight: {
        preflightId: 'preflight-1',
        lifecycle: 'completed',
        profile: 'so101',
        mode: 'teleoperate',
        profileFingerprint: 'profile',
        resourceFingerprint: 'resource',
        createdAt: '2026-07-22T00:00:00Z',
        expiresAt: '2099-07-22T00:00:30Z',
        ownershipComplete: true,
        startEligible: true,
        checks: [],
      },
    })
    const user = userEvent.setup()
    render(<OperatorWorkspace operator={operator} />)

    await user.click(screen.getByRole('button', { name: 'Start Teleoperation' }))

    expect(operator.startSession).toHaveBeenCalledWith(
      'teleoperate',
      expect.objectContaining({ controlFps: 60, maxRelativeTarget: null }),
    )
    expect(screen.queryByRole('button', { name: 'Start Recording' })).not.toBeInTheDocument()
  })

  it('configures a bounded GR00T policy rollout from the operator view', async () => {
    const operator = createOperator({
      capabilities: {
        enabled: true,
        adapterMode: 'lerobot',
        adapterVersion: 1,
        protocolVersion: 2,
        modes: ['teleoperate', 'record', 'policy'],
        profiles: ['so101'],
        cameras: [
          { name: 'wrist', defaultFps: 30 },
          { name: 'front', defaultFps: 30 },
        ],
        preflightEnabled: true,
        sessionStartEnabled: true,
        reason: null,
      },
      preflight: {
        preflightId: 'preflight-policy',
        lifecycle: 'completed',
        profile: 'so101',
        mode: 'policy',
        profileFingerprint: 'profile',
        resourceFingerprint: 'resource',
        createdAt: '2026-07-22T00:00:00Z',
        expiresAt: '2099-07-22T00:00:30Z',
        ownershipComplete: true,
        startEligible: true,
        checks: [],
      },
    })
    const user = userEvent.setup()
    render(<OperatorWorkspace operator={operator} />)

    await user.click(screen.getByRole('tab', { name: 'Policy' }))
    await user.clear(screen.getByLabelText('Task'))
    await user.type(
      screen.getByLabelText('Task'),
      'Pick up the rubber puck on the right bin and place on the front',
    )

    expect(screen.getByLabelText('Rollout seconds')).toHaveValue(30)
    expect(screen.getByLabelText('Follower target clamp')).toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Start Policy' }))

    expect(operator.startSession).toHaveBeenCalledWith(
      'policy',
      expect.objectContaining({
        task: 'Pick up the rubber puck on the right bin and place on the front',
        rolloutTimeS: 30,
        maxRelativeTarget: 2,
      }),
    )
  })

  it('allows transactional acquisition when process visibility is incomplete', () => {
    const operator = createOperator({
      capabilities: {
        enabled: true,
        adapterMode: 'lerobot',
        adapterVersion: 1,
        protocolVersion: 2,
        modes: ['teleoperate'],
        profiles: ['so101'],
        preflightEnabled: true,
        sessionStartEnabled: true,
        reason: null,
      },
      preflight: {
        preflightId: 'preflight-1',
        lifecycle: 'completed',
        profile: 'so101',
        mode: 'teleoperate',
        profileFingerprint: 'profile',
        resourceFingerprint: 'resource',
        createdAt: '2026-07-22T00:00:00Z',
        expiresAt: '2099-07-22T00:00:30Z',
        ownershipComplete: false,
        startEligible: true,
        checks: [
          {
            name: 'device_ownership',
            outcome: 'warning',
            detail: 'No holder found; process visibility is incomplete',
            remediation: null,
          },
        ],
      },
    })

    render(<OperatorWorkspace operator={operator} />)

    expect(screen.getByText('warning')).toBeInTheDocument()
    expect(
      screen.getByText('Preflight is read-only. Starting a session can move the follower arm.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start Teleoperation' })).toBeInTheDocument()
  })

  it('configures script-compatible recording and keyboard episode actions', async () => {
    const operator = createOperator({
      capabilities: {
        enabled: true,
        adapterMode: 'lerobot',
        adapterVersion: 1,
        protocolVersion: 2,
        modes: ['teleoperate', 'record'],
        profiles: ['so101'],
        cameras: [
          { name: 'wrist', defaultFps: 30 },
          { name: 'front', defaultFps: 30 },
        ],
        preflightEnabled: true,
        sessionStartEnabled: true,
        reason: null,
      },
      preflight: {
        preflightId: 'preflight-1',
        lifecycle: 'completed',
        profile: 'so101',
        mode: 'record',
        profileFingerprint: 'profile',
        resourceFingerprint: 'resource',
        createdAt: '2026-07-22T00:00:00Z',
        expiresAt: '2099-07-22T00:00:30Z',
        ownershipComplete: false,
        startEligible: true,
        checks: [],
      },
    })
    const user = userEvent.setup()
    const { rerender } = render(<OperatorWorkspace operator={operator} />)

    await user.click(screen.getByRole('tab', { name: 'Record' }))
    expect(screen.getByLabelText('Control FPS')).toHaveValue(30)
    expect(screen.getByLabelText('Wrist camera FPS')).toHaveValue(30)
    expect(screen.getByLabelText('Front camera FPS')).toHaveValue(30)
    expect(screen.getByLabelText('Follower target clamp')).not.toBeChecked()
    expect(screen.getByLabelText('Save destination')).toHaveTextContent('Local')
    expect(screen.getByLabelText('Dataset name')).toHaveValue('so101-demo')
    expect(screen.getByLabelText('Task')).toHaveValue('Pick <obj> from <loc1> and place in <obj2>')
    expect(screen.getByLabelText('Number of episodes')).toHaveValue(50)

    await user.click(screen.getByRole('button', { name: 'Start Recording' }))
    expect(operator.startSession).toHaveBeenCalledWith(
      'record',
      expect.objectContaining({
        controlFps: 30,
        maxRelativeTarget: null,
        cameraFps: { wrist: 30, front: 30 },
        datasetName: 'so101-demo',
        saveDestination: 'local',
        hubRepoId: null,
        numEpisodes: 50,
      }),
    )

    operator.status = {
      ...operator.status!,
      state: 'running',
      mode: 'record',
      sessionId: 'record-session',
    }
    rerender(<OperatorWorkspace operator={operator} />)
    await user.keyboard('{ArrowRight}{ArrowLeft}{ArrowUp}')

    expect(operator.sendCommand).toHaveBeenNthCalledWith(1, 'save')
    expect(operator.sendCommand).toHaveBeenNthCalledWith(2, 'rerecord')
    expect(operator.sendCommand).toHaveBeenNthCalledWith(3, 'finish')
  })

  it('renders every actuator on one joint plot and a spatial trajectory', () => {
    const jointValues = {
      'shoulder_pan.pos': 12,
      'shoulder_lift.pos': 18,
      'elbow_flex.pos': 24,
      'wrist_flex.pos': 30,
      'wrist_roll.pos': 36,
      'gripper.pos': 42,
    }
    const operator = createOperator({
      telemetry: [
        {
          elapsedS: 0.1,
          leader: jointValues,
          follower: Object.fromEntries(
            Object.entries(jointValues).map(([joint, value]) => [joint, value - 2]),
          ),
          commanded: jointValues,
        },
      ],
    })

    render(<OperatorWorkspace operator={operator} />)

    expect(screen.getByRole('region', { name: 'Joint states' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Telemetry joint')).not.toBeInTheDocument()
    for (const joint of [
      'Shoulder pan',
      'Shoulder lift',
      'Elbow flex',
      'Wrist flex',
      'Wrist roll',
      'Gripper',
    ]) {
      expect(screen.getByText(joint)).toBeInTheDocument()
    }
    expect(screen.getByLabelText('Leader values use dashed lines')).toBeInTheDocument()
    expect(screen.getByLabelText('Follower values use solid lines')).toBeInTheDocument()
    expect(screen.getByLabelText('Commanded values use circle markers')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'End-effector trajectory' })).toBeInTheDocument()
    expect(screen.queryByText('Position over session time')).not.toBeInTheDocument()
    expect(screen.queryByText('SO-101 follower path · metres')).not.toBeInTheDocument()
    expect(screen.getByText('metres')).toBeInTheDocument()
    expect(screen.getByLabelText('Joint state plot').className).toContain('h-80')
    expect(screen.getByLabelText('Trajectory plot frame').className).toContain('aspect-4/3')
  })

  it('places cameras and trajectory in one desktop row with full-width joints below', () => {
    render(<OperatorWorkspace operator={createOperator()} />)

    const visualizationGrid = screen.getByLabelText('Operator visualizations')
    expect(visualizationGrid.className).toContain('xl:grid-cols-3')
    expect(screen.getByLabelText('Joint states').parentElement).not.toBe(visualizationGrid)

    for (const camera of ['wrist', 'front']) {
      const preview = screen.getByLabelText(`${camera} camera preview`)
      expect(preview.className).toContain('aspect-4/3')
      expect(
        screen.getByRole('heading', {
          name: `${camera[0].toUpperCase()}${camera.slice(1)} camera`,
        }),
      ).toBeInTheDocument()
    }
  })
})
