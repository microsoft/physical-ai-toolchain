import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { FilterBuilder, type FilterCondition } from '@/components/curriculum/FilterBuilder'

const numericCondition = (overrides: Partial<FilterCondition> = {}): FilterCondition => ({
  id: 'cond-1',
  field: 'task_completion_rating',
  operator: 'greater_or_equal',
  value: 3,
  ...overrides,
})

const booleanCondition = (overrides: Partial<FilterCondition> = {}): FilterCondition => ({
  id: 'cond-bool',
  field: 'has_anomalies',
  operator: 'is_true',
  value: true,
  ...overrides,
})

describe('FilterBuilder', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('renders empty state when no conditions exist', () => {
    render(<FilterBuilder conditions={[]} onChange={vi.fn()} />)

    expect(
      screen.getByText('No filters applied. Add conditions to filter episodes.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Filter Conditions')).toBeInTheDocument()
    expect(screen.getByText('0')).toBeInTheDocument()
  })

  it('shows the count badge matching the number of conditions', () => {
    render(
      <FilterBuilder
        conditions={[numericCondition(), numericCondition({ id: 'cond-2' })]}
        onChange={vi.fn()}
      />,
    )

    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.queryByText(/No filters applied/)).not.toBeInTheDocument()
  })

  it('appends a default condition when Add Condition is clicked', () => {
    const onChange = vi.fn()
    render(<FilterBuilder conditions={[]} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: /Add Condition/i }))

    expect(onChange).toHaveBeenCalledTimes(1)
    const next = onChange.mock.calls[0][0]
    expect(next).toHaveLength(1)
    expect(next[0]).toMatchObject({
      field: 'task_completion_rating',
      operator: 'greater_or_equal',
      value: 3,
    })
    expect(typeof next[0].id).toBe('string')
    expect(next[0].id).toMatch(/^filter-/)
  })

  it('preserves existing conditions when appending a new one', () => {
    const onChange = vi.fn()
    const existing = numericCondition()
    render(<FilterBuilder conditions={[existing]} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: /Add Condition/i }))

    const next = onChange.mock.calls[0][0]
    expect(next).toHaveLength(2)
    expect(next[0]).toBe(existing)
  })

  it('removes a condition when the X button is clicked', () => {
    const onChange = vi.fn()
    const conditions = [
      numericCondition({ id: 'a' }),
      numericCondition({ id: 'b' }),
      numericCondition({ id: 'c' }),
    ]
    render(<FilterBuilder conditions={conditions} onChange={onChange} />)

    const removeButtons = screen
      .getAllByRole('button')
      .filter((btn) => btn.className.includes('h-8') && btn.className.includes('w-8'))
    expect(removeButtons).toHaveLength(3)

    fireEvent.click(removeButtons[1])

    expect(onChange).toHaveBeenCalledWith([conditions[0], conditions[2]])
  })

  it('renders a number input for numeric conditions and updates value on change', () => {
    const onChange = vi.fn()
    render(<FilterBuilder conditions={[numericCondition({ value: 4 })]} onChange={onChange} />)

    const input = screen.getByDisplayValue('4') as HTMLInputElement
    expect(input.type).toBe('number')

    fireEvent.change(input, { target: { value: '2.5' } })

    expect(onChange).toHaveBeenCalledWith([expect.objectContaining({ id: 'cond-1', value: 2.5 })])
  })

  it('falls back to 0 when the number input is cleared', () => {
    const onChange = vi.fn()
    render(<FilterBuilder conditions={[numericCondition()]} onChange={onChange} />)

    const input = screen.getByDisplayValue('3') as HTMLInputElement
    fireEvent.change(input, { target: { value: '' } })

    expect(onChange).toHaveBeenCalledWith([expect.objectContaining({ value: 0 })])
  })

  it('omits the number input when the field is boolean-typed', () => {
    render(<FilterBuilder conditions={[booleanCondition()]} onChange={vi.fn()} />)

    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
  })

  it('uses step=1 for rating fields', () => {
    render(
      <FilterBuilder
        conditions={[numericCondition({ field: 'task_completion_rating' })]}
        onChange={vi.fn()}
      />,
    )

    const input = screen.getByRole('spinbutton') as HTMLInputElement
    expect(input.step).toBe('1')
  })

  it('uses step=1 for score fields', () => {
    render(
      <FilterBuilder
        conditions={[
          numericCondition({
            field: 'trajectory_quality_score',
            operator: 'greater_than',
            value: 0.8,
          }),
        ]}
        onChange={vi.fn()}
      />,
    )

    const input = screen.getByRole('spinbutton') as HTMLInputElement
    expect(input.step).toBe('1')
  })

  it('uses step=0.1 for non-rating non-score numeric fields', () => {
    render(
      <FilterBuilder
        conditions={[
          numericCondition({
            field: 'smoothness',
            operator: 'greater_than',
            value: 0.5,
          }),
        ]}
        onChange={vi.fn()}
      />,
    )

    const input = screen.getByRole('spinbutton') as HTMLInputElement
    expect(input.step).toBe('0.1')
  })

  it('caps numeric inputs at max=5', () => {
    render(<FilterBuilder conditions={[numericCondition()]} onChange={vi.fn()} />)

    const input = screen.getByRole('spinbutton') as HTMLInputElement
    expect(input.max).toBe('5')
    expect(input.min).toBe('0')
  })

  it('renders the AND connector for every condition after the first', () => {
    render(
      <FilterBuilder
        conditions={[
          numericCondition({ id: 'a' }),
          numericCondition({ id: 'b' }),
          numericCondition({ id: 'c' }),
        ]}
        onChange={vi.fn()}
      />,
    )

    expect(screen.getAllByText('AND')).toHaveLength(2)
  })

  it('does not render an AND connector when only one condition exists', () => {
    render(<FilterBuilder conditions={[numericCondition()]} onChange={vi.fn()} />)

    expect(screen.queryByText('AND')).not.toBeInTheDocument()
  })

  it('applies the className prop to the root container', () => {
    const { container } = render(
      <FilterBuilder conditions={[]} onChange={vi.fn()} className="custom-class" />,
    )

    expect(container.firstChild).toHaveClass('custom-class')
    expect(container.firstChild).toHaveClass('space-y-3')
  })

  it('shows the configured field label for the active condition', () => {
    render(
      <FilterBuilder
        conditions={[numericCondition({ field: 'efficiency', value: 0.7 })]}
        onChange={vi.fn()}
      />,
    )

    expect(screen.getByText('Efficiency')).toBeInTheDocument()
  })

  it('renders boolean operator label when condition uses a boolean field', () => {
    render(
      <FilterBuilder
        conditions={[booleanCondition({ operator: 'is_false', value: false })]}
        onChange={vi.fn()}
      />,
    )

    expect(screen.getByText('Is False')).toBeInTheDocument()
  })

  it('renders the Filter icon and Add Condition button in the header', () => {
    const { container } = render(<FilterBuilder conditions={[]} onChange={vi.fn()} />)

    const header = container.querySelector('.flex.items-center.justify-between')
    expect(header).not.toBeNull()
    expect(within(header as HTMLElement).getByText('Filter Conditions')).toBeInTheDocument()
    expect(
      within(header as HTMLElement).getByRole('button', { name: /Add Condition/i }),
    ).toBeInTheDocument()
  })

  it('renders independent cards per condition', () => {
    const { container } = render(
      <FilterBuilder
        conditions={[numericCondition({ id: 'one' }), booleanCondition({ id: 'two' })]}
        onChange={vi.fn()}
      />,
    )

    const cards = container.querySelectorAll('.p-3')
    expect(cards.length).toBe(2)
  })
})
