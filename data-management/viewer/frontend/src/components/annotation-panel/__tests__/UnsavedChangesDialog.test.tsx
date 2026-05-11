import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { UnsavedChangesDialog } from '../UnsavedChangesDialog'

describe('UnsavedChangesDialog', () => {
  it('renders nothing when closed', () => {
    const { container } = render(
      <UnsavedChangesDialog open={false} onConfirm={vi.fn()} onCancel={vi.fn()} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders three buttons when onSave is provided', () => {
    render(<UnsavedChangesDialog open onConfirm={vi.fn()} onCancel={vi.fn()} onSave={vi.fn()} />)
    expect(screen.getByRole('button', { name: /save and continue/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /discard changes/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /go back/i })).toBeInTheDocument()
  })

  it('omits Save and Continue when onSave is not provided', () => {
    render(<UnsavedChangesDialog open onConfirm={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.queryByRole('button', { name: /save and continue/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /discard changes/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /go back/i })).toBeInTheDocument()
  })

  it('invokes the matching callbacks for each action', async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn()
    const onCancel = vi.fn()
    const onSave = vi.fn()
    render(<UnsavedChangesDialog open onConfirm={onConfirm} onCancel={onCancel} onSave={onSave} />)

    await user.click(screen.getByRole('button', { name: /save and continue/i }))
    expect(onSave).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: /discard changes/i }))
    expect(onConfirm).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: /go back/i }))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })
})
