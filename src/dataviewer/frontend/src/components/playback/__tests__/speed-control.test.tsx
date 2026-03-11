import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SpeedControl } from '@/components/playback/SpeedControl'

afterEach(() => {
  cleanup()
})

describe('SpeedControl', () => {
  it('displays the current speed on the trigger', () => {
    render(<SpeedControl speed={1} onSpeedChange={vi.fn()} />)
    expect(screen.getByLabelText(/playback speed: 1x/i)).toBeInTheDocument()
  })

  it('displays custom speed values on the trigger', () => {
    render(<SpeedControl speed={3.5} onSpeedChange={vi.fn()} />)
    expect(screen.getByLabelText(/playback speed: 3.5x/i)).toBeInTheDocument()
  })

  it('opens popover with preset speed buttons', () => {
    render(<SpeedControl speed={1} onSpeedChange={vi.fn()} />)
    fireEvent.click(screen.getByLabelText(/playback speed/i))
    for (const label of ['0.25x', '0.5x', '0.75x', '1.5x', '2x', '3x', '5x']) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
    // '1x' appears in both trigger and popover
    expect(screen.getAllByText('1x')).toHaveLength(2)
  })

  it('calls onSpeedChange when a preset is clicked', () => {
    const onSpeedChange = vi.fn()
    render(<SpeedControl speed={1} onSpeedChange={onSpeedChange} />)
    fireEvent.click(screen.getByLabelText(/playback speed/i))
    fireEvent.click(screen.getByText('2x'))
    expect(onSpeedChange).toHaveBeenCalledWith(2)
  })

  it('highlights the active preset', () => {
    render(<SpeedControl speed={2} onSpeedChange={vi.fn()} />)
    fireEvent.click(screen.getByLabelText(/playback speed/i))
    // Both trigger and popover contain '2x'; the popover preset is the second one
    const allBtn2x = screen.getAllByText('2x')
    const presetBtn2x = allBtn2x[allBtn2x.length - 1]
    const btn1x = screen.getByText('1x')
    expect(presetBtn2x.className).toContain('bg-primary')
    expect(btn1x.className).not.toContain('bg-primary')
  })

  it('accepts custom speed via the input field', () => {
    const onSpeedChange = vi.fn()
    render(<SpeedControl speed={1} onSpeedChange={onSpeedChange} />)
    fireEvent.click(screen.getByLabelText(/playback speed/i))
    const input = screen.getByLabelText('Custom playback speed')
    fireEvent.change(input, { target: { value: '4' } })
    fireEvent.click(screen.getByText('Set'))
    expect(onSpeedChange).toHaveBeenCalledWith(4)
  })

  it('submits custom speed on Enter key', () => {
    const onSpeedChange = vi.fn()
    render(<SpeedControl speed={1} onSpeedChange={onSpeedChange} />)
    fireEvent.click(screen.getByLabelText(/playback speed/i))
    const input = screen.getByLabelText('Custom playback speed')
    fireEvent.change(input, { target: { value: '7.5' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSpeedChange).toHaveBeenCalledWith(7.5)
  })

  it('clamps custom speed to valid range', () => {
    const onSpeedChange = vi.fn()
    render(<SpeedControl speed={1} onSpeedChange={onSpeedChange} />)
    fireEvent.click(screen.getByLabelText(/playback speed/i))
    const input = screen.getByLabelText('Custom playback speed')
    fireEvent.change(input, { target: { value: '50' } })
    fireEvent.click(screen.getByText('Set'))
    expect(onSpeedChange).toHaveBeenCalledWith(10)
  })

  it('rejects invalid custom speed input', () => {
    const onSpeedChange = vi.fn()
    render(<SpeedControl speed={1} onSpeedChange={onSpeedChange} />)
    fireEvent.click(screen.getByLabelText(/playback speed/i))
    const input = screen.getByLabelText('Custom playback speed')
    fireEvent.change(input, { target: { value: 'abc' } })
    fireEvent.click(screen.getByText('Set'))
    expect(onSpeedChange).not.toHaveBeenCalled()
  })

  it('disables Set button when input is empty', () => {
    render(<SpeedControl speed={1} onSpeedChange={vi.fn()} />)
    fireEvent.click(screen.getByLabelText(/playback speed/i))
    expect(screen.getByText('Set')).toBeDisabled()
  })
})
