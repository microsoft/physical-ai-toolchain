import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { FlagToggle } from '../FlagToggle'

describe('FlagToggle', () => {
  it('renders the label and triggers onToggle when clicked', async () => {
    const user = userEvent.setup()
    const onToggle = vi.fn()
    render(<FlagToggle label="Blurry" active={false} onToggle={onToggle} />)
    await user.click(screen.getByRole('button', { name: /blurry/i }))
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it('renders the shortcut badge when provided and omits it otherwise', () => {
    const { rerender } = render(
      <FlagToggle label="Blurry" active={false} onToggle={vi.fn()} shortcut="B" />,
    )
    expect(screen.getByText('B')).toBeInTheDocument()
    rerender(<FlagToggle label="Blurry" active={false} onToggle={vi.fn()} />)
    expect(screen.queryByText('B')).not.toBeInTheDocument()
  })

  it('applies active styling when active is true', () => {
    const { rerender } = render(<FlagToggle label="Blurry" active onToggle={vi.fn()} />)
    const button = screen.getByRole('button', { name: /blurry/i })
    expect(button.className).toMatch(/bg-red-100/)
    rerender(<FlagToggle label="Blurry" active={false} onToggle={vi.fn()} />)
    expect(screen.getByRole('button', { name: /blurry/i }).className).toMatch(/bg-muted/)
  })
})
