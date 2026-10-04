import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useEpisodeStore, usePlaybackControls, useTrajectoryAdjustmentState } from '@/stores'
import type { TrajectoryPoint, TrajectoryVariable } from '@/types/api'
import type { TrajectoryAdjustment } from '@/types/episode-edit'

import { TrajectoryEditor } from '../TrajectoryEditor'

vi.mock('@/stores', () => ({
  useEpisodeStore: vi.fn(),
  usePlaybackControls: vi.fn(),
  useTrajectoryAdjustmentState: vi.fn(),
}))

vi.mock('@/components/ui/tooltip', () => ({
  Tooltip: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  TooltipTrigger: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  TooltipContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  TooltipProvider: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

const mockedEpisodeStore = vi.mocked(useEpisodeStore)
const mockedPlaybackControls = vi.mocked(usePlaybackControls)
const mockedTrajectoryState = vi.mocked(useTrajectoryAdjustmentState)

interface EpisodeState {
  currentEpisode:
    { trajectoryData: TrajectoryPoint[]; trajectoryVariables?: TrajectoryVariable[] } | undefined
}

type PlaybackState = ReturnType<typeof usePlaybackControls>
type TrajectoryAdjustmentStateValue = ReturnType<typeof useTrajectoryAdjustmentState>

const SINGLE_ARM_NAMES = ['arm_1', 'arm_2', 'arm_3', 'arm_4', 'arm_5', 'arm_6', 'gripper_1']

function makeTrajectoryPoint(seed: number, channels: number): TrajectoryPoint {
  return {
    timestamp: seed,
    jointPositions: Array.from({ length: channels }, (_, i) => 0.1 * (i + 1) + seed),
  } as TrajectoryPoint
}

/** A 7-channel single-arm joint dataset whose state channels carry the dataset's own names. */
function singleArmEpisode(): EpisodeState['currentEpisode'] {
  return {
    trajectoryData: [0, 1, 2].map((seed) => makeTrajectoryPoint(seed, 7)),
    trajectoryVariables: [
      ...SINGLE_ARM_NAMES.map((name, index) => ({
        key: `observation.state[${index}]`,
        label: `State: ${name}`,
        source: 'observation.state',
        index,
        kind: 'state',
      })),
      { key: 'action[0]', label: 'Action: arm_1', source: 'action', index: 0, kind: 'action' },
    ],
  }
}

function setup(
  opts: {
    playback?: Partial<PlaybackState>
    episode?: Partial<EpisodeState>
    trajectory?: Partial<TrajectoryAdjustmentStateValue>
  } = {},
) {
  const playbackState: PlaybackState = {
    currentFrame: 0,
    isPlaying: false,
    playbackSpeed: 1,
    setCurrentFrame: vi.fn(),
    togglePlayback: vi.fn(),
    setPlaybackSpeed: vi.fn(),
    ...opts.playback,
  }
  const episodeState: EpisodeState = { currentEpisode: singleArmEpisode(), ...opts.episode }
  const trajectoryState: TrajectoryAdjustmentStateValue = {
    trajectoryAdjustments: new Map(),
    setTrajectoryAdjustment: vi.fn(),
    removeTrajectoryAdjustment: vi.fn(),
    getTrajectoryAdjustment: vi.fn(),
    clearTrajectoryAdjustments: vi.fn(),
    ...opts.trajectory,
  }
  mockedPlaybackControls.mockReturnValue(playbackState)
  mockedEpisodeStore.mockImplementation(((selector: (state: EpisodeState) => unknown) =>
    selector(episodeState)) as never)
  mockedTrajectoryState.mockReturnValue(trajectoryState)
  return { playbackState, episodeState, trajectoryState }
}

const deltaSlider = (label: string) =>
  screen.getByRole<HTMLInputElement>('slider', { name: `${label} delta` })
const deltaInput = (label: string) =>
  screen.getByRole<HTMLInputElement>('spinbutton', { name: `${label} delta` })
const valueInput = (label: string) =>
  screen.getByRole<HTMLInputElement>('spinbutton', { name: `${label} set value` })

afterEach(() => {
  vi.clearAllMocks()
})

describe('TrajectoryEditor', () => {
  it('renders empty state when no episode is loaded', () => {
    setup({ episode: { currentEpisode: undefined } })
    render(<TrajectoryEditor />)
    expect(screen.getByText('No trajectory data available for this frame.')).toBeInTheDocument()
  })

  it('renders empty state when trajectoryData is empty', () => {
    setup({ episode: { currentEpisode: { trajectoryData: [] } } })
    render(<TrajectoryEditor />)
    expect(screen.getByText('No trajectory data available for this frame.')).toBeInTheDocument()
  })

  it('applies className to empty state container', () => {
    setup({ episode: { currentEpisode: undefined } })
    const { container } = render(<TrajectoryEditor className="custom-empty" />)
    expect(container.querySelector('.custom-empty')).not.toBeNull()
  })

  it('lists one row per state channel, labelled from the dataset state variables', () => {
    setup()
    render(<TrajectoryEditor />)

    expect(screen.getAllByRole('slider')).toHaveLength(7)
    for (const name of SINGLE_ARM_NAMES) {
      expect(deltaSlider(name)).toBeInTheDocument()
      expect(valueInput(name)).toBeInTheDocument()
    }
    expect(screen.queryByText(/Right Arm|Left Arm/)).not.toBeInTheDocument()
  })

  it('labels channels from the joint configuration when the dataset has no names', () => {
    setup({
      episode: { currentEpisode: { trajectoryData: [makeTrajectoryPoint(0, 16)] } },
    })
    render(<TrajectoryEditor />)

    expect(screen.getAllByRole('slider')).toHaveLength(16)
    expect(deltaSlider('Right X')).toBeInTheDocument()
    expect(deltaSlider('Left Gripper')).toBeInTheDocument()
  })

  it('says that exports keep the recorded positions and name the adjusted data for each format', () => {
    setup()
    render(<TrajectoryEditor />)
    expect(screen.getByRole('note')).toHaveTextContent(
      'Exports keep the recorded joint positions and add these adjustments beside them, as qpos_adjusted in HDF5 or adjusted.observation.state in LeRobot, with a mask of the edited rows.',
    )
  })

  it('renders the frame indicator and the modified-frame count', () => {
    const adjustments = new Map<number, TrajectoryAdjustment>([
      [0, { frameIndex: 0, channelDeltas: { 0: 0.1 } }],
      [3, { frameIndex: 3, channelValues: { 6: 0.5 } }],
    ])
    setup({ trajectory: { trajectoryAdjustments: adjustments } })
    render(<TrajectoryEditor />)

    expect(screen.getByText('Frame 0')).toBeInTheDocument()
    expect(screen.getByText('2 frame(s) modified')).toBeInTheDocument()
    expect(screen.getByText('(has adjustments)')).toBeInTheDocument()
  })

  it('omits the "(has adjustments)" badge and disables Apply and Reset Frame for an unedited frame', () => {
    setup()
    render(<TrajectoryEditor />)

    expect(screen.queryByText('(has adjustments)')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Apply to Frame 0/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Reset Frame/ })).toBeDisabled()
  })

  it('applies a channel delta to the current frame', () => {
    const { trajectoryState } = setup({ playback: { currentFrame: 1 } })
    render(<TrajectoryEditor />)

    fireEvent.change(deltaSlider('arm_1'), { target: { value: '0.2' } })
    expect(screen.getByText(/Δ: \+0.2000/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Apply to Frame 1/ }))

    expect(trajectoryState.setTrajectoryAdjustment).toHaveBeenCalledWith(1, {
      channelDeltas: { 0: 0.2 },
      channelValues: undefined,
    })
  })

  it('applies a set value, which replaces the delta on that channel', () => {
    const { trajectoryState } = setup()
    render(<TrajectoryEditor />)

    fireEvent.change(deltaSlider('gripper_1'), { target: { value: '0.1' } })
    fireEvent.change(valueInput('gripper_1'), { target: { value: '0.5' } })
    expect(deltaSlider('gripper_1')).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: /Apply to Frame 0/ }))

    expect(trajectoryState.setTrajectoryAdjustment).toHaveBeenCalledWith(0, {
      channelDeltas: undefined,
      channelValues: { 6: 0.5 },
    })
  })

  it('clears a set value when its input is emptied', () => {
    setup()
    render(<TrajectoryEditor />)

    fireEvent.change(valueInput('gripper_1'), { target: { value: '0.5' } })
    fireEvent.change(valueInput('gripper_1'), { target: { value: '' } })

    expect(deltaSlider('gripper_1')).not.toBeDisabled()
    expect(screen.getByRole('button', { name: /Apply to Frame 0/ })).toBeDisabled()
  })

  it('removes the frame adjustment when Apply runs with nothing left to apply', async () => {
    const user = userEvent.setup()
    const adjustments = new Map<number, TrajectoryAdjustment>([
      [0, { frameIndex: 0, channelDeltas: { 0: 0.1 } }],
    ])
    const { trajectoryState } = setup({ trajectory: { trajectoryAdjustments: adjustments } })
    render(<TrajectoryEditor />)

    fireEvent.change(deltaSlider('arm_1'), { target: { value: '0' } })
    await user.click(screen.getByRole('button', { name: /Apply to Frame/ }))

    expect(trajectoryState.removeTrajectoryAdjustment).toHaveBeenCalledWith(0)
    expect(trajectoryState.setTrajectoryAdjustment).not.toHaveBeenCalled()
  })

  it('removes the frame adjustment when Reset Frame is clicked', async () => {
    const user = userEvent.setup()
    const adjustments = new Map<number, TrajectoryAdjustment>([
      [0, { frameIndex: 0, channelValues: { 6: 0.5 } }],
    ])
    const { trajectoryState } = setup({ trajectory: { trajectoryAdjustments: adjustments } })
    render(<TrajectoryEditor />)

    await user.click(screen.getByRole('button', { name: /Reset Frame/ }))

    expect(trajectoryState.removeTrajectoryAdjustment).toHaveBeenCalledWith(0)
  })

  it('hides Clear All when no frame has adjustments', () => {
    setup()
    render(<TrajectoryEditor />)
    expect(
      screen.queryByRole('button', { name: /Clear All Trajectory Adjustments/ }),
    ).not.toBeInTheDocument()
  })

  it('clears every adjustment from the labelled Clear All button', async () => {
    const user = userEvent.setup()
    const adjustments = new Map<number, TrajectoryAdjustment>([
      [0, { frameIndex: 0, channelDeltas: { 0: 0.1 } }],
      [2, { frameIndex: 2, channelValues: { 6: 0.7 } }],
    ])
    const { trajectoryState } = setup({ trajectory: { trajectoryAdjustments: adjustments } })
    render(<TrajectoryEditor />)

    await user.click(screen.getByRole('button', { name: /Clear All Trajectory Adjustments \(2\)/ }))

    expect(trajectoryState.clearTrajectoryAdjustments).toHaveBeenCalledTimes(1)
  })

  it('still renders the editor when currentFrame is beyond the trajectory', () => {
    setup({ playback: { currentFrame: 999 } })
    render(<TrajectoryEditor />)
    expect(screen.getByText('Frame 999')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Apply to Frame 999/ })).toBeInTheDocument()
  })

  it('shows the stored delta and set value for the current frame', () => {
    const adjustments = new Map<number, TrajectoryAdjustment>([
      [1, { frameIndex: 1, channelDeltas: { 0: 0.25 }, channelValues: { 6: 0.75 } }],
    ])
    setup({ playback: { currentFrame: 1 }, trajectory: { trajectoryAdjustments: adjustments } })
    render(<TrajectoryEditor />)

    expect(deltaInput('arm_1').value).toBe('0.2500')
    expect(valueInput('gripper_1').value).toBe('0.7500')
  })

  it('loads frame-specific adjustments when the current frame changes', () => {
    const adjustments = new Map<number, TrajectoryAdjustment>([
      [1, { frameIndex: 1, channelDeltas: { 0: 0.25 } }],
    ])
    const { playbackState } = setup({ trajectory: { trajectoryAdjustments: adjustments } })
    const { rerender } = render(<TrajectoryEditor />)
    expect(deltaInput('arm_1').value).toBe('0.0000')

    mockedPlaybackControls.mockReturnValue({ ...playbackState, currentFrame: 1 })
    rerender(<TrajectoryEditor />)

    expect(deltaInput('arm_1').value).toBe('0.2500')
  })

  it('keeps pending edits when another frame gains an adjustment', () => {
    const { trajectoryState } = setup()
    const { rerender } = render(<TrajectoryEditor />)

    fireEvent.change(deltaInput('arm_1'), { target: { value: '0.25' } })
    expect(deltaInput('arm_1').value).toBe('0.2500')

    // A different frame gains an adjustment, replacing the Map identity.
    mockedTrajectoryState.mockReturnValue({
      ...trajectoryState,
      trajectoryAdjustments: new Map<number, TrajectoryAdjustment>([
        [5, { frameIndex: 5, channelDeltas: { 0: 0.9 } }],
      ]),
    })
    rerender(<TrajectoryEditor />)

    expect(screen.getByText(/Δ: \+0.2500/)).toBeInTheDocument()
  })

  it('clears the editor inputs when all adjustments are cleared', async () => {
    const user = userEvent.setup()
    const adjustments = new Map<number, TrajectoryAdjustment>([
      [0, { frameIndex: 0, channelDeltas: { 0: 0.25 } }],
    ])
    const { trajectoryState } = setup({ trajectory: { trajectoryAdjustments: adjustments } })
    const { rerender } = render(<TrajectoryEditor />)
    expect(deltaInput('arm_1').value).toBe('0.2500')

    await user.click(screen.getByRole('button', { name: /Clear All Trajectory Adjustments/i }))
    expect(trajectoryState.clearTrajectoryAdjustments).toHaveBeenCalled()

    mockedTrajectoryState.mockReturnValue({ ...trajectoryState, trajectoryAdjustments: new Map() })
    rerender(<TrajectoryEditor />)

    expect(deltaInput('arm_1').value).toBe('0.0000')
  })

  it('ignores non-numeric delta text and restores the formatted value on blur', () => {
    setup()
    render(<TrajectoryEditor />)

    fireEvent.change(deltaInput('arm_2'), { target: { value: 'abc' } })
    expect(screen.getByRole('button', { name: /Apply to Frame/ })).toBeDisabled()

    fireEvent.blur(deltaInput('arm_2'))
    expect(deltaInput('arm_2').value).toBe('0.0000')
  })

  it('resets only the chosen channel from its row', async () => {
    const user = userEvent.setup()
    setup()
    render(<TrajectoryEditor />)

    fireEvent.change(deltaSlider('arm_1'), { target: { value: '0.3' } })
    fireEvent.change(deltaSlider('arm_2'), { target: { value: '-0.2' } })
    await user.click(screen.getByRole('button', { name: 'Reset arm_1' }))

    expect(screen.queryByText(/Δ: \+0.3000/)).not.toBeInTheDocument()
    expect(screen.getByText(/Δ: -0.2000/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reset arm_3' })).not.toBeInTheDocument()
  })
})
