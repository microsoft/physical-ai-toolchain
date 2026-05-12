import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ActionButtons } from '../ActionButtons'

function renderActionButtons(overrides: Partial<React.ComponentProps<typeof ActionButtons>> = {}) {
  const props = {
    isSaving: false,
    isDirty: false,
    onSaveAndAdvance: vi.fn(),
    onSkip: vi.fn(),
    onFlagForReview: vi.fn(),
    onSave: vi.fn(),
    ...overrides,
  }
  const utils = render(<ActionButtons {...props} />)
  return { ...utils, props }
}

describe('ActionButtons', () => {
  it('renders all four action buttons', () => {
    renderActionButtons()
    expect(screen.getByRole('button', { name: /save & next/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /skip/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /save only/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /flag for review/i })).toBeInTheDocument()
  })

  it('shows the unsaved changes banner only when dirty', () => {
    const { rerender, props } = renderActionButtons({ isDirty: true })
    expect(screen.getByText(/unsaved changes/i)).toBeInTheDocument()
    rerender(<ActionButtons {...props} isDirty={false} />)
    expect(screen.queryByText(/unsaved changes/i)).not.toBeInTheDocument()
  })

  it('invokes the matching callbacks when buttons are clicked', async () => {
    const user = userEvent.setup()
    const { props } = renderActionButtons({ isDirty: true })

    await user.click(screen.getByRole('button', { name: /save & next/i }))
    expect(props.onSaveAndAdvance).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: /skip/i }))
    expect(props.onSkip).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: /save only/i }))
    expect(props.onSave).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: /flag for review/i }))
    expect(props.onFlagForReview).toHaveBeenCalledTimes(1)
  })

  it('shows Saving... and disables Save & Next while saving', () => {
    renderActionButtons({ isSaving: true, isDirty: true })
    const saveNext = screen.getByRole('button', { name: /saving/i })
    expect(saveNext).toBeDisabled()
  })

  it('disables Save Only when not dirty', () => {
    renderActionButtons({ isDirty: false })
    expect(screen.getByRole('button', { name: /save only/i })).toBeDisabled()
  })

  it('applies a custom className to the root element', () => {
    const { container } = renderActionButtons({ className: 'custom-root' })
    expect(container.firstChild).toHaveClass('custom-root')
  })
})
