import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useEpisodeStore, usePlaybackControls, useTrajectoryAdjustmentState } from '@/stores'
import type { TrajectoryPoint } from '@/types/api'
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
  currentEpisode: { trajectoryData: TrajectoryPoint[] } | undefined
}

type PlaybackState = ReturnType<typeof usePlaybackControls>
type TrajectoryAdjustmentStateValue = ReturnType<typeof useTrajectoryAdjustmentState>

function makeTrajectoryPoint(seed: number): TrajectoryPoint {
  return {
    timestamp: seed,
    jointPositions: Array.from({ length: 16 }, (_, i) => 0.1 * (i + 1) + seed),
  } as TrajectoryPoint
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
  const episodeState: EpisodeState = {
    currentEpisode: {
      trajectoryData: [makeTrajectoryPoint(0), makeTrajectoryPoint(1), makeTrajectoryPoint(2)],
    },
    ...opts.episode,
  }
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

  it('renders frame indicator with current frame', () => {
    setup({ playback: { currentFrame: 0 } })
    render(<TrajectoryEditor />)
    expect(screen.getByText('Frame 0')).toBeInTheDocument()
  })

  it('renders Right Arm and Left Arm sections', () => {
    setup()
    render(<TrajectoryEditor />)
    expect(screen.getByText('Right Arm')).toBeInTheDocument()
    expect(screen.getByText('Left Arm')).toBeInTheDocument()
  })

  it('renders X, Y, Z axis labels for each arm', () => {
    setup()
    render(<TrajectoryEditor />)
    expect(screen.getAllByText('X')).toHaveLength(2)
    expect(screen.getAllByText('Y')).toHaveLength(2)
    expect(screen.getAllByText('Z')).toHaveLength(2)
  })

  it('renders Gripper labels for both arms', () => {
    setup()
    render(<TrajectoryEditor />)
    expect(screen.getAllByText('Gripper')).toHaveLength(2)
  })

  it('renders zero counter when no adjustments exist', () => {
    setup()
    render(<TrajectoryEditor />)
    expect(screen.getByText('0 frame(s) modified')).toBeInTheDocument()
  })

  it('renders adjustments counter reflecting map size', () => {
    const adjustments = new Map<number, TrajectoryAdjustment>([
      [0, { frameIndex: 0, rightArmDelta: [0.1, 0, 0] }],
      [3, { frameIndex: 3, leftGripperOverride: 0.5 }],
    ])
    setup({ trajectory: { trajectoryAdjustments: adjustments } })
    render(<TrajectoryEditor />)
    expect(screen.getByText('2 frame(s) modified')).toBeInTheDocument()
  })

  it('renders "(has adjustments)" badge when current frame has an adjustment', () => {
    const adjustments = new Map<number, TrajectoryAdjustment>([
      [0, { frameIndex: 0, rightArmDelta: [0.1, 0, 0] }],
    ])
    setup({ playback: { currentFrame: 0 }, trajectory: { trajectoryAdjustments: adjustments } })
    render(<TrajectoryEditor />)
    expect(screen.getByText('(has adjustments)')).toBeInTheDocument()
  })

  it('does not render "(has adjustments)" badge when current frame has no adjustment', () => {
    setup()
    render(<TrajectoryEditor />)
    expect(screen.queryByText('(has adjustments)')).not.toBeInTheDocument()
  })

  it('renders Apply button labelled with current frame', () => {
    setup({ playback: { currentFrame: 2 } })
    render(<TrajectoryEditor />)
    expect(screen.getByRole('button', { name: /Apply to Frame 2/ })).toBeInTheDocument()
  })

  it('disables Apply and Reset Frame buttons when no changes and no adjustment', () => {
    setup()
    render(<TrajectoryEditor />)
    expect(screen.getByRole('button', { name: /Apply to Frame/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Reset Frame/ })).toBeDisabled()
  })

  it('enables Apply and Reset Frame buttons when current frame has an existing adjustment', () => {
    const adjustments = new Map<number, TrajectoryAdjustment>([
      [0, { frameIndex: 0, rightGripperOverride: 0.5 }],
    ])
    setup({ trajectory: { trajectoryAdjustments: adjustments } })
    render(<TrajectoryEditor />)
    expect(screen.getByRole('button', { name: /Apply to Frame/ })).not.toBeDisabled()
    expect(screen.getByRole('button', { name: /Reset Frame/ })).not.toBeDisabled()
  })

  it('updates display when a range input changes and shows delta annotation', () => {
    setup()
    render(<TrajectoryEditor />)

    const ranges = screen
      .getAllByRole('slider')
      .filter((el) => (el as HTMLInputElement).max === '0.5')
    expect(ranges.length).toBeGreaterThanOrEqual(3)
    fireEvent.change(ranges[0], { target: { value: '0.1' } })

    expect(screen.getByText(/Δ: \+0.1000/)).toBeInTheDocument()
  })

  it('calls setTrajectoryAdjustment with computed payload when Apply clicked after a change', () => {
    const { trajectoryState } = setup({ playback: { currentFrame: 1 } })
    render(<TrajectoryEditor />)

    const ranges = screen
      .getAllByRole('slider')
      .filter((el) => (el as HTMLInputElement).max === '0.5')
    fireEvent.change(ranges[0], { target: { value: '0.2' } })

    fireEvent.click(screen.getByRole('button', { name: /Apply to Frame 1/ }))

    expect(trajectoryState.setTrajectoryAdjustment).toHaveBeenCalledTimes(1)
    const [frameArg, payload] = vi.mocked(trajectoryState.setTrajectoryAdjustment).mock.calls[0]
    expect(frameArg).toBe(1)
    expect(payload.rightArmDelta).toEqual([0.2, 0, 0])
    expect(payload.leftArmDelta).toBeUndefined()
    expect(payload.rightGripperOverride).toBeUndefined()
    expect(payload.leftGripperOverride).toBeUndefined()
  })

  it('calls removeTrajectoryAdjustment when Apply clicked with no changes but existing adjustment', async () => {
    const user = userEvent.setup()
    const adjustments = new Map<number, TrajectoryAdjustment>([
      [0, { frameIndex: 0, rightArmDelta: [0.1, 0, 0] }],
    ])
    const { trajectoryState } = setup({ trajectory: { trajectoryAdjustments: adjustments } })
    render(<TrajectoryEditor />)

    const ranges = screen
      .getAllByRole('slider')
      .filter((el) => (el as HTMLInputElement).max === '0.5')
    fireEvent.change(ranges[0], { target: { value: '0' } })

    await user.click(screen.getByRole('button', { name: /Apply to Frame/ }))

    expect(trajectoryState.removeTrajectoryAdjustment).toHaveBeenCalledWith(0)
    expect(trajectoryState.setTrajectoryAdjustment).not.toHaveBeenCalled()
  })

  it('calls removeTrajectoryAdjustment when Reset Frame is clicked', async () => {
    const user = userEvent.setup()
    const adjustments = new Map<number, TrajectoryAdjustment>([
      [0, { frameIndex: 0, rightArmDelta: [0.1, 0, 0] }],
    ])
    const { trajectoryState } = setup({ trajectory: { trajectoryAdjustments: adjustments } })
    render(<TrajectoryEditor />)

    await user.click(screen.getByRole('button', { name: /Reset Frame/ }))

    expect(trajectoryState.removeTrajectoryAdjustment).toHaveBeenCalledWith(0)
  })

  it('hides Clear All button when no adjustments exist', () => {
    setup()
    render(<TrajectoryEditor />)
    expect(
      screen.queryByRole('button', { name: /Clear All Trajectory Adjustments/ }),
    ).not.toBeInTheDocument()
  })

  it('shows Clear All button labelled with adjustment count when adjustments exist', () => {
    const adjustments = new Map<number, TrajectoryAdjustment>([
      [0, { frameIndex: 0, rightArmDelta: [0.1, 0, 0] }],
      [2, { frameIndex: 2, leftGripperOverride: 0.7 }],
    ])
    setup({ trajectory: { trajectoryAdjustments: adjustments } })
    render(<TrajectoryEditor />)
    expect(
      screen.getByRole('button', { name: /Clear All Trajectory Adjustments \(2\)/ }),
    ).toBeInTheDocument()
  })

  it('calls clearTrajectoryAdjustments when Clear All is clicked', async () => {
    const user = userEvent.setup()
    const adjustments = new Map<number, TrajectoryAdjustment>([
      [0, { frameIndex: 0, rightArmDelta: [0.1, 0, 0] }],
    ])
    const { trajectoryState } = setup({ trajectory: { trajectoryAdjustments: adjustments } })
    render(<TrajectoryEditor />)

    await user.click(screen.getByRole('button', { name: /Clear All Trajectory Adjustments/ }))

    expect(trajectoryState.clearTrajectoryAdjustments).toHaveBeenCalledTimes(1)
  })

  it('clamps currentFrame above trajectory length and still renders editor UI', () => {
    setup({ playback: { currentFrame: 999 } })
    render(<TrajectoryEditor />)
    expect(screen.getByText('Frame 999')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Apply to Frame 999/ })).toBeInTheDocument()
  })

  it('updates gripper override when gripper range input changes', () => {
    setup()
    render(<TrajectoryEditor />)

    const gripperRanges = screen
      .getAllByRole('slider')
      .filter((el) => (el as HTMLInputElement).max === '1')
    expect(gripperRanges.length).toBeGreaterThanOrEqual(2)
    fireEvent.change(gripperRanges[0], { target: { value: '0.42' } })

    expect(screen.getByRole('button', { name: /Apply to Frame/ })).not.toBeDisabled()
  })

  it('applies className to root container', () => {
    setup()
    const { container } = render(<TrajectoryEditor className="custom-trajectory" />)
    expect(container.querySelector('.custom-trajectory')).not.toBeNull()
  })

  it('updates left arm delta when its slider changes and shows delta annotation', () => {
    setup()
    render(<TrajectoryEditor />)

    // Eight axis sliders total; the second batch of three drives Left Arm
    const axisSliders = screen
      .getAllByRole('slider')
      .filter((el) => (el as HTMLInputElement).max === '0.5')
    expect(axisSliders.length).toBe(6)
    fireEvent.change(axisSliders[3], { target: { value: '-0.25' } })

    expect(screen.getByText(/Δ: -0.2500/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Apply to Frame/ })).not.toBeDisabled()
  })

  it('AxisInput numeric input updates delta and Δ annotation on change', () => {
    setup()
    render(<TrajectoryEditor />)

    const numberInputs = screen
      .getAllByRole('spinbutton')
      .filter((el) => (el as HTMLInputElement).step === '0.001')
    expect(numberInputs.length).toBe(6)
    fireEvent.change(numberInputs[1], { target: { value: '0.123' } })

    expect(screen.getByText(/Δ: \+0.1230/)).toBeInTheDocument()
  })

  it('AxisInput numeric input ignores non-numeric text', () => {
    setup()
    render(<TrajectoryEditor />)

    const numberInputs = screen
      .getAllByRole('spinbutton')
      .filter((el) => (el as HTMLInputElement).step === '0.001')
    fireEvent.change(numberInputs[0], { target: { value: 'abc' } })

    // type=number coerces invalid input to empty string and parseFloat returns NaN
    expect((numberInputs[0] as HTMLInputElement).value).toBe('')
    expect(screen.getByRole('button', { name: /Apply to Frame/ })).toBeDisabled()
  })

  it('AxisInput onBlur restores the formatted delta value after invalid input clears it', () => {
    setup()
    render(<TrajectoryEditor />)

    const numberInputs = screen
      .getAllByRole('spinbutton')
      .filter((el) => (el as HTMLInputElement).step === '0.001')
    const input = numberInputs[0] as HTMLInputElement
    fireEvent.change(input, { target: { value: 'abc' } })
    expect(input.value).toBe('')

    fireEvent.blur(input)
    expect(input.value).toBe('0.0000')
  })

  it('AxisInput renders existing delta when frame already has an adjustment', () => {
    const adjustments = new Map<number, TrajectoryAdjustment>([
      [1, { frameIndex: 1, rightArmDelta: [0.25, 0, 0] }],
    ])
    setup({ playback: { currentFrame: 1 }, trajectory: { trajectoryAdjustments: adjustments } })
    render(<TrajectoryEditor />)

    const numberInputs = screen
      .getAllByRole('spinbutton')
      .filter((el) => (el as HTMLInputElement).step === '0.001')
    expect((numberInputs[0] as HTMLInputElement).value).toBe('0.2500')
  })

  it('ArmEditor per-arm reset button resets only the right arm delta', async () => {
    const user = userEvent.setup()
    setup()
    render(<TrajectoryEditor />)

    const axisSliders = screen
      .getAllByRole('slider')
      .filter((el) => (el as HTMLInputElement).max === '0.5')
    fireEvent.change(axisSliders[0], { target: { value: '0.3' } })
    fireEvent.change(axisSliders[3], { target: { value: '-0.2' } })

    const rightHeader = screen.getByText('Right Arm').parentElement as HTMLElement
    const resetBtn = rightHeader.querySelector('button.h-6.w-6') as HTMLButtonElement
    expect(resetBtn).toBeTruthy()
    await user.click(resetBtn)

    // Right Arm Δ annotation gone, Left Arm Δ remains
    expect(screen.queryByText(/Δ: \+0.3000/)).not.toBeInTheDocument()
    expect(screen.getByText(/Δ: -0.2000/)).toBeInTheDocument()
  })

  it('ArmEditor per-arm reset button shows only when arm has changes', () => {
    setup()
    render(<TrajectoryEditor />)
    expect(screen.queryByText('Reset Right Arm adjustments')).not.toBeInTheDocument()
    expect(screen.queryByText('Reset Left Arm adjustments')).not.toBeInTheDocument()

    const axisSliders = screen
      .getAllByRole('slider')
      .filter((el) => (el as HTMLInputElement).max === '0.5')
    fireEvent.change(axisSliders[0], { target: { value: '0.1' } })

    expect(screen.getByText('Reset Right Arm adjustments')).toBeInTheDocument()
    expect(screen.queryByText('Reset Left Arm adjustments')).not.toBeInTheDocument()
  })

  it('ArmEditor gripper numeric input updates the override on valid input', () => {
    setup()
    render(<TrajectoryEditor />)

    const gripperInputs = screen
      .getAllByRole('spinbutton')
      .filter((el) => (el as HTMLInputElement).step === '0.01')
    expect(gripperInputs.length).toBe(2)

    fireEvent.change(gripperInputs[0], { target: { value: '0.75' } })

    expect(screen.getByRole('button', { name: /Apply to Frame/ })).not.toBeDisabled()
  })

  it('ArmEditor gripper numeric input ignores non-numeric text', () => {
    setup()
    render(<TrajectoryEditor />)

    const gripperInputs = screen
      .getAllByRole('spinbutton')
      .filter((el) => (el as HTMLInputElement).step === '0.01')
    fireEvent.change(gripperInputs[0], { target: { value: 'xyz' } })

    expect((gripperInputs[0] as HTMLInputElement).value).toBe('')
    expect(screen.getByRole('button', { name: /Apply to Frame/ })).toBeDisabled()
  })

  it('ArmEditor gripper input onBlur restores formatted value after invalid input clears it', () => {
    setup()
    render(<TrajectoryEditor />)

    const gripperInputs = screen
      .getAllByRole('spinbutton')
      .filter((el) => (el as HTMLInputElement).step === '0.01')
    const input = gripperInputs[0] as HTMLInputElement
    const initialValue = input.value
    fireEvent.change(input, { target: { value: 'xyz' } })
    expect(input.value).toBe('')

    fireEvent.blur(input)
    expect(input.value).toBe(initialValue)
  })

  it('ArmEditor gripper clear button appears only when an override is set', () => {
    setup()
    const { container, rerender } = render(<TrajectoryEditor />)

    // Initially no clear button is rendered next to the gripper inputs
    const gripperInputsBefore = container.querySelectorAll('input[type="number"][min="0"][max="1"]')
    expect(gripperInputsBefore.length).toBe(2)
    const initialClearButtons = container.querySelectorAll('button.h-7.w-7')
    expect(initialClearButtons.length).toBe(0)

    const gripperSlider = screen
      .getAllByRole('slider')
      .find((el) => (el as HTMLInputElement).max === '1') as HTMLInputElement
    fireEvent.change(gripperSlider, { target: { value: '0.5' } })

    rerender(<TrajectoryEditor />)
    const clearButtons = container.querySelectorAll('button.h-7.w-7')
    expect(clearButtons.length).toBe(1)
  })

  it('ArmEditor gripper clear button click removes the override', async () => {
    const user = userEvent.setup()
    setup()
    const { container } = render(<TrajectoryEditor />)

    const gripperSlider = screen
      .getAllByRole('slider')
      .find((el) => (el as HTMLInputElement).max === '1') as HTMLInputElement
    fireEvent.change(gripperSlider, { target: { value: '0.5' } })

    const clearButton = container.querySelector('button.h-7.w-7') as HTMLButtonElement
    expect(clearButton).toBeTruthy()
    await user.click(clearButton)

    // Apply button toggles back to disabled because no overrides remain
    expect(screen.getByRole('button', { name: /Apply to Frame/ })).toBeDisabled()
  })

  it('renders existing right arm adjustment in the AxisInput display', () => {
    const adjustments = new Map<number, TrajectoryAdjustment>([
      [0, { frameIndex: 0, rightArmDelta: [0.123, -0.456, 0.789] }],
    ])
    setup({ trajectory: { trajectoryAdjustments: adjustments } })
    render(<TrajectoryEditor />)

    expect(screen.getByText(/Δ: \+0.1230/)).toBeInTheDocument()
    expect(screen.getByText(/Δ: -0.4560/)).toBeInTheDocument()
    expect(screen.getByText(/Δ: \+0.7890/)).toBeInTheDocument()
  })

  it('renders existing gripper override values from store on initial mount', () => {
    const adjustments = new Map<number, TrajectoryAdjustment>([
      [0, { frameIndex: 0, rightGripperOverride: 0.42, leftGripperOverride: 0.18 }],
    ])
    setup({ trajectory: { trajectoryAdjustments: adjustments } })
    render(<TrajectoryEditor />)

    const gripperInputs = screen
      .getAllByRole('spinbutton')
      .filter((el) => (el as HTMLInputElement).step === '0.01')
    expect((gripperInputs[0] as HTMLInputElement).value).toBe('0.420')
    expect((gripperInputs[1] as HTMLInputElement).value).toBe('0.180')
  })
})
