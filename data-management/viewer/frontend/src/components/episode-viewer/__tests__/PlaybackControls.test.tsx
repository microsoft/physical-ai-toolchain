import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { usePlaybackControls } from '@/stores'

import { PlaybackControls } from '../PlaybackControls'

vi.mock('@/stores', () => ({
  usePlaybackControls: vi.fn(),
}))

const mockedUsePlaybackControls = vi.mocked(usePlaybackControls)

interface ControlsState {
  currentFrame: number
  isPlaying: boolean
  playbackSpeed: number
  setCurrentFrame: (frame: number) => void
  togglePlayback: () => void
  setPlaybackSpeed: (speed: number) => void
}

function createState(overrides: Partial<ControlsState> = {}): ControlsState {
  return {
    currentFrame: 0,
    isPlaying: false,
    playbackSpeed: 1,
    setCurrentFrame: vi.fn(),
    togglePlayback: vi.fn(),
    setPlaybackSpeed: vi.fn(),
    ...overrides,
  }
}

describe('PlaybackControls', () => {
  beforeEach(() => {
    mockedUsePlaybackControls.mockReset()
  })

  it('shows the Play action label when paused', () => {
    mockedUsePlaybackControls.mockReturnValue(createState({ isPlaying: false }))
    render(<PlaybackControls currentFrame={10} totalFrames={100} duration={10} fps={30} />)
    expect(screen.getByRole('button', { name: 'Play' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Pause' })).not.toBeInTheDocument()
  })

  it('shows the Pause action label while playing', () => {
    mockedUsePlaybackControls.mockReturnValue(createState({ isPlaying: true }))
    render(<PlaybackControls currentFrame={10} totalFrames={100} duration={10} fps={30} />)
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument()
  })

  it('toggles playback when the play/pause button is clicked', async () => {
    const state = createState({ isPlaying: false })
    mockedUsePlaybackControls.mockReturnValue(state)
    const user = userEvent.setup()
    render(<PlaybackControls currentFrame={10} totalFrames={100} duration={10} fps={30} />)

    await user.click(screen.getByRole('button', { name: 'Play' }))
    expect(vi.mocked(state.togglePlayback)).toHaveBeenCalledTimes(1)
  })

  it('clamps the previous-frame action at zero', async () => {
    const state = createState()
    mockedUsePlaybackControls.mockReturnValue(state)
    const user = userEvent.setup()
    render(<PlaybackControls currentFrame={0} totalFrames={100} duration={10} fps={30} />)

    await user.click(screen.getByRole('button', { name: 'Previous frame' }))
    expect(vi.mocked(state.setCurrentFrame)).toHaveBeenCalledWith(0)
  })

  it('advances to the next frame without applying an upper clamp', async () => {
    const state = createState()
    mockedUsePlaybackControls.mockReturnValue(state)
    const user = userEvent.setup()
    render(<PlaybackControls currentFrame={99} totalFrames={100} duration={10} fps={30} />)

    await user.click(screen.getByRole('button', { name: 'Next frame' }))
    expect(vi.mocked(state.setCurrentFrame)).toHaveBeenCalledWith(100)
  })

  it('jumps to the start and end frames via the boundary controls', async () => {
    const state = createState()
    mockedUsePlaybackControls.mockReturnValue(state)
    const user = userEvent.setup()
    render(<PlaybackControls currentFrame={42} totalFrames={120} duration={10} fps={30} />)

    await user.click(screen.getByRole('button', { name: 'Go to start' }))
    expect(vi.mocked(state.setCurrentFrame)).toHaveBeenLastCalledWith(0)

    await user.click(screen.getByRole('button', { name: 'Go to end' }))
    expect(vi.mocked(state.setCurrentFrame)).toHaveBeenLastCalledWith(119)
  })

  it('formats the elapsed and total time as mm:ss', () => {
    mockedUsePlaybackControls.mockReturnValue(createState())
    // currentFrame=150 at 30fps => 5s elapsed; duration=65s => "1:05"
    render(<PlaybackControls currentFrame={150} totalFrames={1950} duration={65} fps={30} />)
    expect(screen.getByText('0:05 / 1:05')).toBeInTheDocument()
  })
})
