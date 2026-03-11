import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { PlaybackControls } from '@/components/episode-viewer/PlaybackControls'
import { useEpisodeStore } from '@/stores'

afterEach(() => {
  cleanup()
  useEpisodeStore.getState().reset()
})

describe('PlaybackControls', () => {
  const defaultProps = {
    currentFrame: 100,
    totalFrames: 385,
    duration: 12.833,
    fps: 30,
  }

  it('renders frame navigation buttons', () => {
    render(<PlaybackControls {...defaultProps} />)
    expect(screen.getByTitle('Go to start')).toBeInTheDocument()
    expect(screen.getByTitle('Go to end')).toBeInTheDocument()
  })

  it('renders play/pause toggle', () => {
    render(<PlaybackControls {...defaultProps} />)
    expect(screen.getByTitle(/play|pause/i)).toBeInTheDocument()
  })

  it('displays formatted time', () => {
    render(<PlaybackControls {...defaultProps} />)
    // 100 / 30 = 3.33s → "0:03" ; 12.833s → "0:12"
    expect(screen.getByText(/0:03/)).toBeInTheDocument()
  })

  it('renders speed control trigger', () => {
    render(<PlaybackControls {...defaultProps} />)
    expect(screen.getByLabelText(/playback speed/i)).toBeInTheDocument()
  })

  it('play button uses icon-only variant with consistent size', () => {
    render(<PlaybackControls {...defaultProps} />)
    const playButton = screen.getByTitle(/play/i)
    // Button should use size="icon" for consistent dimensions
    expect(playButton).toBeInTheDocument()
    // Should not contain text that changes width
    expect(playButton.textContent).toBe('')
  })

  it('clicking a speed preset updates store playbackSpeed', async () => {
    render(<PlaybackControls {...defaultProps} />)
    // Open speed popover
    fireEvent.click(screen.getByLabelText(/playback speed/i))
    // Click 2x preset
    fireEvent.click(screen.getByText('2x'))
    expect(useEpisodeStore.getState().playbackSpeed).toBe(2)
  })

  it('displays the current speed on the trigger', () => {
    useEpisodeStore.getState().setPlaybackSpeed(2)
    render(<PlaybackControls {...defaultProps} />)
    expect(screen.getByLabelText(/playback speed: 2x/i)).toBeInTheDocument()
  })

  it('switching speeds updates the store', () => {
    render(<PlaybackControls {...defaultProps} />)
    fireEvent.click(screen.getByLabelText(/playback speed/i))
    fireEvent.click(screen.getByText('2x'))
    expect(useEpisodeStore.getState().playbackSpeed).toBe(2)

    fireEvent.click(screen.getByLabelText(/playback speed/i))
    fireEvent.click(screen.getByText('0.5x'))
    expect(useEpisodeStore.getState().playbackSpeed).toBe(0.5)
  })

  it('renders speed presets in the popover', () => {
    render(<PlaybackControls {...defaultProps} />)
    fireEvent.click(screen.getByLabelText(/playback speed/i))
    for (const label of ['0.25x', '0.5x', '0.75x', '1.5x', '2x', '3x', '5x']) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
    // '1x' appears on trigger and in popover
    expect(screen.getAllByText('1x')).toHaveLength(2)
  })
})
