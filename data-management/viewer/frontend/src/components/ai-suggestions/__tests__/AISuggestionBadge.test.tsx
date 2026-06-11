import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { AISuggestionBadge } from '../AISuggestionBadge'

describe('AISuggestionBadge', () => {
  it('renders error label when hasError is true', () => {
    render(<AISuggestionBadge hasError />)
    expect(screen.getByText('Error')).toBeInTheDocument()
    expect(screen.getByRole('button')).toBeDisabled()
  })

  it('renders applied label when isAccepted is true', () => {
    render(<AISuggestionBadge isAccepted confidence={0.9} />)
    expect(screen.getByText('Applied')).toBeInTheDocument()
  })

  it('renders analyzing label and spinner when loading', () => {
    const { container } = render(<AISuggestionBadge isLoading />)
    expect(screen.getByText('Analyzing...')).toBeInTheDocument()
    expect(screen.getByRole('button')).toBeDisabled()
    expect(container.querySelector('.animate-spin')).toBeInTheDocument()
  })

  it('renders no data label when confidence is undefined', () => {
    render(<AISuggestionBadge />)
    expect(screen.getByText('No data')).toBeInTheDocument()
  })

  it('renders high confidence label when confidence >= 0.8', () => {
    render(<AISuggestionBadge confidence={0.85} />)
    expect(screen.getByText('High confidence')).toBeInTheDocument()
  })

  it('renders medium confidence label when 0.5 <= confidence < 0.8', () => {
    render(<AISuggestionBadge confidence={0.6} />)
    expect(screen.getByText('Medium confidence')).toBeInTheDocument()
  })

  it('renders low confidence label when confidence < 0.5', () => {
    render(<AISuggestionBadge confidence={0.2} />)
    expect(screen.getByText('Low confidence')).toBeInTheDocument()
  })

  it('calls onClick when enabled and clicked', () => {
    const onClick = vi.fn()
    render(<AISuggestionBadge confidence={0.9} onClick={onClick} />)
    fireEvent.click(screen.getByRole('button'))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('does not call onClick when disabled (loading)', () => {
    const onClick = vi.fn()
    render(<AISuggestionBadge isLoading onClick={onClick} />)
    fireEvent.click(screen.getByRole('button'))
    expect(onClick).not.toHaveBeenCalled()
  })

  it('does not call onClick when disabled (error)', () => {
    const onClick = vi.fn()
    render(<AISuggestionBadge hasError onClick={onClick} />)
    fireEvent.click(screen.getByRole('button'))
    expect(onClick).not.toHaveBeenCalled()
  })

  it('applies cursor-pointer class when clickable and enabled', () => {
    render(<AISuggestionBadge confidence={0.9} onClick={vi.fn()} />)
    expect(screen.getByRole('button').className).toContain('cursor-pointer')
  })

  it('applies cursor-default class when disabled', () => {
    render(<AISuggestionBadge hasError onClick={vi.fn()} />)
    expect(screen.getByRole('button').className).toContain('cursor-default')
  })

  it('forwards className prop', () => {
    render(<AISuggestionBadge confidence={0.9} className="custom-badge" />)
    expect(screen.getByRole('button').className).toContain('custom-badge')
  })

  it('renders a button with type="button"', () => {
    render(<AISuggestionBadge confidence={0.9} />)
    expect(screen.getByRole('button')).toHaveAttribute('type', 'button')
  })

  it('renders sparkles icon when not loading', () => {
    const { container } = render(<AISuggestionBadge confidence={0.9} />)
    expect(container.querySelector('.animate-spin')).not.toBeInTheDocument()
    expect(container.querySelector('svg')).toBeInTheDocument()
  })
})
