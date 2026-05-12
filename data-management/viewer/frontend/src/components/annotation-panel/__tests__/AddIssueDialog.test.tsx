import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AddIssueDialog } from '../AddIssueDialog'

describe('AddIssueDialog', () => {
  it('renders nothing when open is false', () => {
    const { container } = render(
      <AddIssueDialog open={false} onClose={vi.fn()} onAdd={vi.fn()} currentFrame={5} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders the dialog title and form fields when open', () => {
    render(<AddIssueDialog open onClose={vi.fn()} onAdd={vi.fn()} currentFrame={5} />)
    expect(screen.getByText('Add Data Quality Issue')).toBeInTheDocument()
    expect(screen.getByLabelText('Start frame')).toHaveValue(5)
    expect(screen.getByLabelText('End frame')).toHaveValue(15)
    expect(screen.getByLabelText('Notes (optional)')).toBeInTheDocument()
  })

  it('closes when the X button is clicked', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<AddIssueDialog open onClose={onClose} onAdd={vi.fn()} currentFrame={0} />)
    const buttons = screen.getAllByRole('button')
    await user.click(buttons[0])
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('closes when Cancel is clicked', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<AddIssueDialog open onClose={onClose} onAdd={vi.fn()} currentFrame={0} />)
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('submits with default values and notes undefined when empty', async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    const onClose = vi.fn()
    render(<AddIssueDialog open onClose={onClose} onAdd={onAdd} currentFrame={20} />)
    await user.click(screen.getByRole('button', { name: 'Add Issue' }))
    expect(onAdd).toHaveBeenCalledWith({
      type: 'frame-drop',
      severity: 'minor',
      notes: undefined,
      affectedFrames: [20, 30],
    })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('submits notes when provided and respects severity selection', async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    render(<AddIssueDialog open onClose={vi.fn()} onAdd={onAdd} currentFrame={0} />)
    await user.click(screen.getByRole('button', { name: 'critical' }))
    await user.type(screen.getByLabelText('Notes (optional)'), 'sensor noise spike')
    await user.click(screen.getByRole('button', { name: 'Add Issue' }))
    expect(onAdd).toHaveBeenCalledWith({
      type: 'frame-drop',
      severity: 'critical',
      notes: 'sensor noise spike',
      affectedFrames: [0, 10],
    })
  })

  it('updates frame inputs and submits the edited values', async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    render(<AddIssueDialog open onClose={vi.fn()} onAdd={onAdd} currentFrame={0} />)
    const start = screen.getByLabelText('Start frame')
    const end = screen.getByLabelText('End frame')
    await user.clear(start)
    await user.type(start, '100')
    await user.clear(end)
    await user.type(end, '200')
    await user.click(screen.getByRole('button', { name: 'Add Issue' }))
    expect(onAdd).toHaveBeenCalledWith({
      type: 'frame-drop',
      severity: 'minor',
      notes: undefined,
      affectedFrames: [100, 200],
    })
  })

  it('clears Start frame to 0 when input is emptied', async () => {
    const user = userEvent.setup()
    render(<AddIssueDialog open onClose={vi.fn()} onAdd={vi.fn()} currentFrame={5} />)
    const start = screen.getByLabelText('Start frame')
    await user.clear(start)
    expect(start).toHaveValue(0)
  })

  it('selects major severity when clicked', async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    render(<AddIssueDialog open onClose={vi.fn()} onAdd={onAdd} currentFrame={0} />)
    await user.click(screen.getByRole('button', { name: 'major' }))
    await user.click(screen.getByRole('button', { name: 'Add Issue' }))
    expect(onAdd).toHaveBeenCalledWith(expect.objectContaining({ severity: 'major' }))
  })
})
