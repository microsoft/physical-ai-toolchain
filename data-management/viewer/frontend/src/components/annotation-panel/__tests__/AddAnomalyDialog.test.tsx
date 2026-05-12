import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AddAnomalyDialog } from '../AddAnomalyDialog'

describe('AddAnomalyDialog', () => {
  it('renders nothing when open is false', () => {
    const { container } = render(
      <AddAnomalyDialog open={false} onClose={vi.fn()} onAdd={vi.fn()} currentFrame={42} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('renders with default frame range based on currentFrame', () => {
    render(<AddAnomalyDialog open onClose={vi.fn()} onAdd={vi.fn()} currentFrame={42} />)
    expect(screen.getAllByText('Add Anomaly').length).toBeGreaterThan(0)
    expect(screen.getByLabelText('Start frame')).toHaveValue(42)
    expect(screen.getByLabelText('End frame')).toHaveValue(52)
  })

  it('calls onClose when the X icon button is clicked', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<AddAnomalyDialog open onClose={onClose} onAdd={vi.fn()} currentFrame={0} />)
    const buttons = screen.getAllByRole('button')
    await user.click(buttons[0])
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('calls onClose when Cancel is clicked', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<AddAnomalyDialog open onClose={onClose} onAdd={vi.fn()} currentFrame={0} />)
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('submits with defaults and a derived description when description is empty', async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    const onClose = vi.fn()
    render(<AddAnomalyDialog open onClose={onClose} onAdd={onAdd} currentFrame={30} />)
    await user.click(screen.getByRole('button', { name: 'Add Anomaly' }))
    expect(onAdd).toHaveBeenCalledWith({
      type: 'unexpected-stop',
      severity: 'medium',
      description: 'unexpected stop detected',
      frameRange: [30, 40],
      timestamp: [1, 40 / 30],
      verified: true,
      autoDetected: false,
    })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('submits the typed description when provided and respects severity selection', async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    render(<AddAnomalyDialog open onClose={vi.fn()} onAdd={onAdd} currentFrame={60} />)

    const severityGroup = screen.getByRole('group', { name: /severity/i })
    await user.click(within(severityGroup).getByRole('button', { name: /high/i }))

    await user.type(screen.getByLabelText(/description/i), 'gripper slipped')
    await user.click(screen.getByRole('button', { name: 'Add Anomaly' }))

    expect(onAdd).toHaveBeenCalledWith(
      expect.objectContaining({
        severity: 'high',
        description: 'gripper slipped',
        frameRange: [60, 70],
      }),
    )
  })

  it('updates frame inputs and submits the edited values', async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    render(<AddAnomalyDialog open onClose={vi.fn()} onAdd={onAdd} currentFrame={0} />)

    const start = screen.getByLabelText('Start frame')
    const end = screen.getByLabelText('End frame')
    await user.clear(start)
    await user.type(start, '15')
    await user.clear(end)
    await user.type(end, '90')

    await user.click(screen.getByRole('button', { name: 'Add Anomaly' }))
    expect(onAdd).toHaveBeenCalledWith(
      expect.objectContaining({ frameRange: [15, 90], timestamp: [0.5, 3] }),
    )
  })

  it('falls back to 0 when frame input is cleared', async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    render(<AddAnomalyDialog open onClose={vi.fn()} onAdd={onAdd} currentFrame={5} />)
    const start = screen.getByLabelText('Start frame')
    await user.clear(start)
    expect(start).toHaveValue(0)
    await user.click(screen.getByRole('button', { name: 'Add Anomaly' }))
    expect(onAdd).toHaveBeenCalledWith(expect.objectContaining({ frameRange: [0, 15] }))
  })

  it('uses the MapPin buttons to set start/end to the current frame', async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    render(<AddAnomalyDialog open onClose={vi.fn()} onAdd={onAdd} currentFrame={50} />)

    const start = screen.getByLabelText('Start frame')
    const end = screen.getByLabelText('End frame')
    await user.clear(start)
    await user.type(start, '10')
    await user.clear(end)
    await user.type(end, '20')

    const startPin = screen.getByTitle('Use current frame as start')
    await user.click(startPin)
    // current=50, frameEnd=20<50 so end resets to 60
    expect(start).toHaveValue(50)
    expect(end).toHaveValue(60)

    await user.clear(start)
    await user.type(start, '90')
    const endPin = screen.getByTitle('Use current frame as end')
    await user.click(endPin)
    // current=50, frameStart=90>50 so start resets to max(0, 40)
    expect(end).toHaveValue(50)
    expect(start).toHaveValue(40)
  })

  it('resets frame range when currentFrame changes while open', () => {
    const { rerender } = render(
      <AddAnomalyDialog open onClose={vi.fn()} onAdd={vi.fn()} currentFrame={10} />,
    )
    expect(screen.getByLabelText('Start frame')).toHaveValue(10)
    rerender(<AddAnomalyDialog open onClose={vi.fn()} onAdd={vi.fn()} currentFrame={100} />)
    expect(screen.getByLabelText('Start frame')).toHaveValue(100)
    expect(screen.getByLabelText('End frame')).toHaveValue(110)
  })
})
