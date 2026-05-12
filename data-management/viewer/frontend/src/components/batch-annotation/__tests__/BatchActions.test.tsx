import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { BatchActions } from '@/components/batch-annotation/BatchActions'

const baseProps = () => ({
  selectedCount: 0,
  totalCount: 100,
  isProcessing: false,
  progress: 0,
  onSelectAll: vi.fn(),
  onClearSelection: vi.fn(),
  onApplyRating: vi.fn(),
  onApplyQuality: vi.fn(),
})

describe('BatchActions', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('renders selection count text', () => {
    render(<BatchActions {...baseProps()} selectedCount={3} totalCount={42} />)
    expect(screen.getByText('3 of 42 selected')).toBeInTheDocument()
  })

  it('enables Select All by default and calls onSelectAll when clicked', () => {
    const onSelectAll = vi.fn()
    render(<BatchActions {...baseProps()} onSelectAll={onSelectAll} />)
    const button = screen.getByRole('button', { name: /Select All/i })
    expect(button).not.toBeDisabled()
    fireEvent.click(button)
    expect(onSelectAll).toHaveBeenCalledTimes(1)
  })

  it('disables Select All when isProcessing is true', () => {
    render(<BatchActions {...baseProps()} isProcessing />)
    expect(screen.getByRole('button', { name: /Select All/i })).toBeDisabled()
  })

  it('disables Clear button when no selection exists', () => {
    render(<BatchActions {...baseProps()} selectedCount={0} />)
    expect(screen.getByRole('button', { name: /Clear/i })).toBeDisabled()
  })

  it('enables Clear when selectedCount > 0 and calls onClearSelection', () => {
    const onClearSelection = vi.fn()
    render(<BatchActions {...baseProps()} selectedCount={2} onClearSelection={onClearSelection} />)
    const button = screen.getByRole('button', { name: /Clear/i })
    expect(button).not.toBeDisabled()
    fireEvent.click(button)
    expect(onClearSelection).toHaveBeenCalledTimes(1)
  })

  it('disables Clear when isProcessing even with selection', () => {
    render(<BatchActions {...baseProps()} selectedCount={5} isProcessing />)
    expect(screen.getByRole('button', { name: /Clear/i })).toBeDisabled()
  })

  it('disables rating and quality buttons when no selection', () => {
    render(<BatchActions {...baseProps()} selectedCount={0} />)
    expect(screen.getByRole('button', { name: /Success/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Partial/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Failure/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /3★/ })).toBeDisabled()
  })

  it('invokes onApplyRating with success immediately when selectedCount <= 10', () => {
    const onApplyRating = vi.fn()
    render(<BatchActions {...baseProps()} selectedCount={5} onApplyRating={onApplyRating} />)
    fireEvent.click(screen.getByRole('button', { name: /Success/i }))
    expect(onApplyRating).toHaveBeenCalledWith('success')
    expect(screen.queryByText('Confirm Batch Action')).not.toBeInTheDocument()
  })

  it('invokes onApplyRating with partial immediately when small selection', () => {
    const onApplyRating = vi.fn()
    render(<BatchActions {...baseProps()} selectedCount={3} onApplyRating={onApplyRating} />)
    fireEvent.click(screen.getByRole('button', { name: /Partial/i }))
    expect(onApplyRating).toHaveBeenCalledWith('partial')
  })

  it('invokes onApplyRating with failure immediately when small selection', () => {
    const onApplyRating = vi.fn()
    render(<BatchActions {...baseProps()} selectedCount={1} onApplyRating={onApplyRating} />)
    fireEvent.click(screen.getByRole('button', { name: /Failure/i }))
    expect(onApplyRating).toHaveBeenCalledWith('failure')
  })

  it('invokes onApplyQuality with score immediately when small selection', () => {
    const onApplyQuality = vi.fn()
    render(<BatchActions {...baseProps()} selectedCount={2} onApplyQuality={onApplyQuality} />)
    fireEvent.click(screen.getByRole('button', { name: /4★/ }))
    expect(onApplyQuality).toHaveBeenCalledWith(4)
  })

  it('renders all five quality star buttons', () => {
    render(<BatchActions {...baseProps()} selectedCount={1} />)
    for (const score of [1, 2, 3, 4, 5]) {
      expect(screen.getByRole('button', { name: `${score}★` })).toBeInTheDocument()
    }
  })

  it('shows confirmation dialog when selectedCount > 10 for rating', () => {
    const onApplyRating = vi.fn()
    render(<BatchActions {...baseProps()} selectedCount={15} onApplyRating={onApplyRating} />)
    fireEvent.click(screen.getByRole('button', { name: /Success/i }))
    expect(onApplyRating).not.toHaveBeenCalled()
    expect(screen.getByText('Confirm Batch Action')).toBeInTheDocument()
    expect(screen.getByText(/Mark 15 episodes as Success\?/)).toBeInTheDocument()
  })

  it('shows confirmation dialog when selectedCount > 10 for quality', () => {
    const onApplyQuality = vi.fn()
    render(<BatchActions {...baseProps()} selectedCount={20} onApplyQuality={onApplyQuality} />)
    fireEvent.click(screen.getByRole('button', { name: /5★/ }))
    expect(onApplyQuality).not.toHaveBeenCalled()
    expect(screen.getByText(/Set quality to 5 stars for 20 episodes\?/)).toBeInTheDocument()
  })

  it('cancels confirmation without invoking action', () => {
    const onApplyRating = vi.fn()
    render(<BatchActions {...baseProps()} selectedCount={15} onApplyRating={onApplyRating} />)
    fireEvent.click(screen.getByRole('button', { name: /Success/i }))
    fireEvent.click(screen.getByRole('button', { name: /^Cancel$/ }))
    expect(onApplyRating).not.toHaveBeenCalled()
    expect(screen.queryByText('Confirm Batch Action')).not.toBeInTheDocument()
  })

  it('confirms action and invokes callback then hides dialog', () => {
    const onApplyRating = vi.fn()
    render(<BatchActions {...baseProps()} selectedCount={15} onApplyRating={onApplyRating} />)
    fireEvent.click(screen.getByRole('button', { name: /Failure/i }))
    fireEvent.click(screen.getByRole('button', { name: /^Confirm$/ }))
    expect(onApplyRating).toHaveBeenCalledWith('failure')
    expect(screen.queryByText('Confirm Batch Action')).not.toBeInTheDocument()
  })

  it('confirms quality action and invokes onApplyQuality', () => {
    const onApplyQuality = vi.fn()
    render(<BatchActions {...baseProps()} selectedCount={11} onApplyQuality={onApplyQuality} />)
    fireEvent.click(screen.getByRole('button', { name: /3★/ }))
    fireEvent.click(screen.getByRole('button', { name: /^Confirm$/ }))
    expect(onApplyQuality).toHaveBeenCalledWith(3)
  })

  it('renders progress bar with width matching progress prop when isProcessing', () => {
    const { container } = render(<BatchActions {...baseProps()} isProcessing progress={42} />)
    expect(screen.getByText('Processing batch...')).toBeInTheDocument()
    expect(screen.getByText('42%')).toBeInTheDocument()
    const bar = container.querySelector('.bg-primary.h-full') as HTMLElement
    expect(bar).not.toBeNull()
    expect(bar.getAttribute('style')).toContain('width: 42%')
  })

  it('does not render progress bar when not processing', () => {
    render(<BatchActions {...baseProps()} progress={0} />)
    expect(screen.queryByText('Processing batch...')).not.toBeInTheDocument()
  })

  it('disables all action buttons while processing', () => {
    render(<BatchActions {...baseProps()} selectedCount={5} isProcessing />)
    expect(screen.getByRole('button', { name: /Success/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Partial/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Failure/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /1★/ })).toBeDisabled()
  })
})
