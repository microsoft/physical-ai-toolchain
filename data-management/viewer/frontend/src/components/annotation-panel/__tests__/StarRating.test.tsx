import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { StarRating } from '@/components/annotation-panel/StarRating'

afterEach(() => {
  cleanup()
})

describe('StarRating', () => {
  it('renders five star buttons by default with radiogroup role', () => {
    render(<StarRating value={0} onChange={() => {}} />)
    expect(screen.getByRole('radiogroup')).toBeInTheDocument()
    expect(screen.getAllByRole('radio')).toHaveLength(5)
  })

  it('respects max prop when rendering star count', () => {
    render(<StarRating value={0} onChange={() => {}} max={3} />)
    expect(screen.getAllByRole('radio')).toHaveLength(3)
  })

  it('uses singular star label for the first star', () => {
    render(<StarRating value={0} onChange={() => {}} />)
    expect(screen.getByRole('radio', { name: '1 star' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: '2 stars' })).toBeInTheDocument()
  })

  it('marks the star matching value as checked', () => {
    render(<StarRating value={3} onChange={() => {}} />)
    expect(screen.getByRole('radio', { name: '3 stars' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('radio', { name: '2 stars' })).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('radio', { name: '4 stars' })).toHaveAttribute('aria-checked', 'false')
  })

  it('invokes onChange with the clicked rating', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<StarRating value={0} onChange={onChange} />)
    await user.click(screen.getByRole('radio', { name: '4 stars' }))
    expect(onChange).toHaveBeenCalledWith(4)
  })

  it('disables interaction when readOnly is true', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<StarRating value={2} onChange={onChange} readOnly />)
    const star = screen.getByRole('radio', { name: '5 stars' })
    expect(star).toBeDisabled()
    await user.click(star)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('applies the provided label as the radiogroup aria-label', () => {
    render(<StarRating value={0} onChange={() => {}} label="Quality rating" />)
    expect(screen.getByRole('radiogroup')).toHaveAttribute('aria-label', 'Quality rating')
  })
})
