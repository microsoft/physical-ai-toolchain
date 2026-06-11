import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useLabelStore } from '@/stores/label-store'

import { LabelFilter } from '../LabelFilter'

const initialState = useLabelStore.getState()

beforeEach(() => {
  useLabelStore.setState({
    ...initialState,
    availableLabels: ['alpha', 'beta'],
    filterLabels: [],
    isLoaded: true,
  })
})

afterEach(() => {
  useLabelStore.setState(initialState, true)
})

describe('LabelFilter', () => {
  it('renders nothing while the store is not loaded', () => {
    useLabelStore.setState({ isLoaded: false })
    const { container } = render(<LabelFilter />)
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing when there are no available labels', () => {
    useLabelStore.setState({ availableLabels: [], isLoaded: true })
    const { container } = render(<LabelFilter />)
    expect(container.firstChild).toBeNull()
  })

  it('renders the header and a badge per available label', () => {
    render(<LabelFilter />)
    expect(screen.getByText(/filter by label/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /alpha/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /beta/i })).toBeInTheDocument()
  })

  it('toggles the filter label when clicked', async () => {
    const user = userEvent.setup()
    render(<LabelFilter />)
    await user.click(screen.getByRole('button', { name: /alpha/i }))
    expect(useLabelStore.getState().filterLabels).toContain('alpha')
  })

  it('reflects active state for filter labels', () => {
    useLabelStore.setState({ filterLabels: ['alpha'] })
    render(<LabelFilter />)
    const alphaBtn = screen.getByRole('button', { name: /alpha/i })
    // active variant renders the X icon as an svg child
    expect(alphaBtn.querySelector('svg')).not.toBeNull()
  })
})
