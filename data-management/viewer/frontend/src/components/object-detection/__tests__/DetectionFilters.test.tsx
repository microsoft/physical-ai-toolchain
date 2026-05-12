import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { DetectionFilters } from '../DetectionFilters'

describe('DetectionFilters', () => {
  const baseFilters = { classes: [], minConfidence: 0.25 }

  it('displays the current confidence as a percentage', () => {
    render(
      <DetectionFilters
        filters={{ classes: [], minConfidence: 0.42 }}
        availableClasses={['person']}
        onFiltersChange={vi.fn()}
      />,
    )

    expect(screen.getByText('42%')).toBeInTheDocument()
  })

  it('invokes onFiltersChange with parsed confidence on slider change', () => {
    const onFiltersChange = vi.fn()
    render(
      <DetectionFilters
        filters={baseFilters}
        availableClasses={['person']}
        onFiltersChange={onFiltersChange}
      />,
    )

    const slider = screen.getByRole('slider') as HTMLInputElement
    fireEvent.change(slider, { target: { value: '0.5' } })

    expect(onFiltersChange).toHaveBeenCalledWith({ classes: [], minConfidence: 0.5 })
  })

  it('shows empty state when no classes are available', () => {
    render(
      <DetectionFilters filters={baseFilters} availableClasses={[]} onFiltersChange={vi.fn()} />,
    )

    expect(screen.getByText('No classes detected yet')).toBeInTheDocument()
  })

  it('renders a checkbox for each available class', () => {
    render(
      <DetectionFilters
        filters={baseFilters}
        availableClasses={['person', 'car', 'dog']}
        onFiltersChange={vi.fn()}
      />,
    )

    expect(screen.getByLabelText('person')).toBeInTheDocument()
    expect(screen.getByLabelText('car')).toBeInTheDocument()
    expect(screen.getByLabelText('dog')).toBeInTheDocument()
  })

  it('treats empty filter classes as "all selected" (checkboxes appear checked)', () => {
    render(
      <DetectionFilters
        filters={{ classes: [], minConfidence: 0.25 }}
        availableClasses={['person', 'car']}
        onFiltersChange={vi.fn()}
      />,
    )

    expect(screen.getByLabelText('person')).toHaveAttribute('data-state', 'checked')
    expect(screen.getByLabelText('car')).toHaveAttribute('data-state', 'checked')
  })

  it('only marks an explicitly included class as checked', () => {
    render(
      <DetectionFilters
        filters={{ classes: ['person'], minConfidence: 0.25 }}
        availableClasses={['person', 'car']}
        onFiltersChange={vi.fn()}
      />,
    )

    expect(screen.getByLabelText('person')).toHaveAttribute('data-state', 'checked')
    expect(screen.getByLabelText('car')).toHaveAttribute('data-state', 'unchecked')
  })

  it('adds a class to filters when an unchecked checkbox is toggled', () => {
    const onFiltersChange = vi.fn()
    render(
      <DetectionFilters
        filters={{ classes: ['person'], minConfidence: 0.25 }}
        availableClasses={['person', 'car']}
        onFiltersChange={onFiltersChange}
      />,
    )

    fireEvent.click(screen.getByLabelText('car'))

    expect(onFiltersChange).toHaveBeenCalledWith({
      classes: ['person', 'car'],
      minConfidence: 0.25,
    })
  })

  it('removes a class from filters when a checked checkbox is toggled', () => {
    const onFiltersChange = vi.fn()
    render(
      <DetectionFilters
        filters={{ classes: ['person', 'car'], minConfidence: 0.25 }}
        availableClasses={['person', 'car']}
        onFiltersChange={onFiltersChange}
      />,
    )

    fireEvent.click(screen.getByLabelText('person'))

    expect(onFiltersChange).toHaveBeenCalledWith({
      classes: ['car'],
      minConfidence: 0.25,
    })
  })

  it('clears class filters when "All" is clicked (empty array means all visible)', () => {
    const onFiltersChange = vi.fn()
    render(
      <DetectionFilters
        filters={{ classes: ['person'], minConfidence: 0.5 }}
        availableClasses={['person', 'car']}
        onFiltersChange={onFiltersChange}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'All' }))

    expect(onFiltersChange).toHaveBeenCalledWith({ classes: [], minConfidence: 0.5 })
  })

  it('selects every available class when "None" is clicked', () => {
    const onFiltersChange = vi.fn()
    render(
      <DetectionFilters
        filters={{ classes: [], minConfidence: 0.5 }}
        availableClasses={['person', 'car']}
        onFiltersChange={onFiltersChange}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'None' }))

    expect(onFiltersChange).toHaveBeenCalledWith({
      classes: ['person', 'car'],
      minConfidence: 0.5,
    })
  })

  it('resets to default filters when "Reset Filters" is clicked', () => {
    const onFiltersChange = vi.fn()
    render(
      <DetectionFilters
        filters={{ classes: ['person'], minConfidence: 0.9 }}
        availableClasses={['person', 'car']}
        onFiltersChange={onFiltersChange}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Reset Filters' }))

    expect(onFiltersChange).toHaveBeenCalledWith({ classes: [], minConfidence: 0.25 })
  })
})
