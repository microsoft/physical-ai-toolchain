import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BatchGrid } from '@/components/batch-annotation/BatchGrid'
import { useBatchSelection } from '@/hooks/use-batch-selection'
import type { EpisodeMeta } from '@/types/api'

vi.mock('@/hooks/use-batch-selection', () => ({
  useBatchSelection: vi.fn(),
}))

const buildEpisodes = (count: number): EpisodeMeta[] =>
  Array.from({ length: count }, (_, i) => ({
    index: i,
    length: 50 + i,
    taskIndex: 0,
    hasAnnotations: false,
    annotationStatus: i % 3 === 0 ? 'complete' : i % 3 === 1 ? 'pending' : 'in-progress',
  }))

interface SelectionMockOverrides {
  selectedIndices?: Set<number>
  selectedCount?: number
  selectedArray?: number[]
  hasSelection?: boolean
  lastClickedIndex?: number | null
  toggleSelection?: ReturnType<typeof vi.fn>
  selectRange?: ReturnType<typeof vi.fn>
  selectAll?: ReturnType<typeof vi.fn>
  clearSelection?: ReturnType<typeof vi.fn>
}

const setSelectionMock = (overrides: SelectionMockOverrides = {}) => {
  const value = {
    selectedIndices: overrides.selectedIndices ?? new Set<number>(),
    isSelecting: false,
    lastClickedIndex: overrides.lastClickedIndex ?? null,
    toggleSelection: overrides.toggleSelection ?? vi.fn(),
    selectRange: overrides.selectRange ?? vi.fn(),
    selectAll: overrides.selectAll ?? vi.fn(),
    clearSelection: overrides.clearSelection ?? vi.fn(),
    isSelected: vi.fn(),
    setLastClickedIndex: vi.fn(),
    setSelecting: vi.fn(),
    selectedCount: overrides.selectedCount ?? 0,
    selectedArray: overrides.selectedArray ?? [],
    hasSelection: overrides.hasSelection ?? false,
  }
  vi.mocked(useBatchSelection).mockReturnValue(
    value as unknown as ReturnType<typeof useBatchSelection>,
  )
  return value
}

const baseProps = (episodes: EpisodeMeta[] = buildEpisodes(5)) => ({
  episodes,
  onQuickRate: vi.fn(),
  onOpenEpisode: vi.fn(),
  onBatchRate: vi.fn().mockResolvedValue(undefined),
  onBatchQuality: vi.fn().mockResolvedValue(undefined),
})

describe('BatchGrid', () => {
  beforeEach(() => {
    setSelectionMock()
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('renders status filter buttons with episode counts', () => {
    const episodes = buildEpisodes(9)
    render(<BatchGrid {...baseProps(episodes)} />)
    expect(screen.getByRole('button', { name: /All \(9\)/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Pending \(3\)/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Complete \(3\)/ })).toBeInTheDocument()
  })

  it('renders an episode card per episode (capped at PAGE_SIZE)', () => {
    const episodes = buildEpisodes(5)
    render(<BatchGrid {...baseProps(episodes)} />)
    for (let i = 0; i < 5; i += 1) {
      expect(screen.getByText(`Episode ${i}`)).toBeInTheDocument()
    }
  })

  it('limits page size to 24 episodes per page', () => {
    const episodes = buildEpisodes(30)
    render(<BatchGrid {...baseProps(episodes)} />)
    expect(screen.getByText('Episode 0')).toBeInTheDocument()
    expect(screen.getByText('Episode 23')).toBeInTheDocument()
    expect(screen.queryByText('Episode 24')).not.toBeInTheDocument()
  })

  it('does not render pagination when totalPages <= 1', () => {
    render(<BatchGrid {...baseProps(buildEpisodes(5))} />)
    expect(screen.queryByRole('button', { name: /Previous/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Next/i })).not.toBeInTheDocument()
  })

  it('renders pagination and disables Previous on first page', () => {
    const episodes = buildEpisodes(30)
    render(<BatchGrid {...baseProps(episodes)} />)
    expect(screen.getByText('Page 1 of 2')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Previous/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Next/i })).not.toBeDisabled()
  })

  it('navigates to next page and disables Next on the last page', () => {
    const episodes = buildEpisodes(30)
    render(<BatchGrid {...baseProps(episodes)} />)
    fireEvent.click(screen.getByRole('button', { name: /Next/i }))
    expect(screen.getByText('Page 2 of 2')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Next/i })).toBeDisabled()
    expect(screen.getByText('Episode 24')).toBeInTheDocument()
  })

  it('navigates back to previous page', () => {
    const episodes = buildEpisodes(30)
    render(<BatchGrid {...baseProps(episodes)} />)
    fireEvent.click(screen.getByRole('button', { name: /Next/i }))
    fireEvent.click(screen.getByRole('button', { name: /Previous/i }))
    expect(screen.getByText('Page 1 of 2')).toBeInTheDocument()
  })

  it('filters episodes by pending status', () => {
    const episodes = buildEpisodes(9)
    render(<BatchGrid {...baseProps(episodes)} />)
    fireEvent.click(screen.getByRole('button', { name: /Pending \(3\)/ }))
    expect(screen.getAllByText(/^Episode \d+$/)).toHaveLength(3)
    expect(screen.getByText('Episode 0')).toBeInTheDocument()
    expect(screen.getByText('Episode 1')).toBeInTheDocument()
    expect(screen.getByText('Episode 2')).toBeInTheDocument()
    expect(screen.queryByText('Episode 3')).not.toBeInTheDocument()
  })

  it('filters episodes by complete status', () => {
    const episodes = buildEpisodes(9)
    render(<BatchGrid {...baseProps(episodes)} />)
    fireEvent.click(screen.getByRole('button', { name: /Complete \(3\)/ }))
    expect(screen.getAllByText(/^Episode \d+$/)).toHaveLength(3)
    expect(screen.getByText('Episode 0')).toBeInTheDocument()
    expect(screen.getByText('Episode 1')).toBeInTheDocument()
    expect(screen.getByText('Episode 2')).toBeInTheDocument()
    expect(screen.queryByText('Episode 3')).not.toBeInTheDocument()
  })

  it('returns to All filter to show every episode', () => {
    const episodes = buildEpisodes(9)
    render(<BatchGrid {...baseProps(episodes)} />)
    fireEvent.click(screen.getByRole('button', { name: /Pending \(3\)/ }))
    fireEvent.click(screen.getByRole('button', { name: /All \(9\)/ }))
    for (let i = 0; i < 9; i += 1) {
      expect(screen.getByText(`Episode ${i}`)).toBeInTheDocument()
    }
  })

  it('changes column layout when column buttons are clicked', () => {
    const episodes = buildEpisodes(5)
    const { container } = render(<BatchGrid {...baseProps(episodes)} />)
    const grid = container.querySelector('div.grid.gap-4') as HTMLElement
    expect(grid.className).toContain('grid-cols-3')

    const allH8Buttons = Array.from(container.querySelectorAll('button.h-8.w-8'))
    const colButtons = allH8Buttons.slice(-3)
    fireEvent.click(colButtons[0])
    expect((container.querySelector('div.grid.gap-4') as HTMLElement).className).toContain(
      'grid-cols-2',
    )

    fireEvent.click(colButtons[2])
    expect((container.querySelector('div.grid.gap-4') as HTMLElement).className).toContain(
      'grid-cols-4',
    )

    fireEvent.click(colButtons[1])
    expect((container.querySelector('div.grid.gap-4') as HTMLElement).className).toContain(
      'grid-cols-3',
    )
  })

  it('handleSelectAll passes current page indices to selectAll', () => {
    const selectAll = vi.fn()
    setSelectionMock({ selectAll })
    const episodes = buildEpisodes(30)
    render(<BatchGrid {...baseProps(episodes)} />)
    fireEvent.click(screen.getByRole('button', { name: /Select All/i }))
    expect(selectAll).toHaveBeenCalledTimes(1)
    expect(selectAll).toHaveBeenCalledWith(Array.from({ length: 24 }, (_, i) => i))
  })

  it('handleSelectAll uses page-2 indices when on page 2', () => {
    const selectAll = vi.fn()
    setSelectionMock({ selectAll })
    const episodes = buildEpisodes(30)
    render(<BatchGrid {...baseProps(episodes)} />)
    fireEvent.click(screen.getByRole('button', { name: /Next/i }))
    fireEvent.click(screen.getByRole('button', { name: /Select All/i }))
    expect(selectAll).toHaveBeenCalledWith([24, 25, 26, 27, 28, 29])
  })

  it('clicking an episode card without shift toggles selection', () => {
    const toggleSelection = vi.fn()
    setSelectionMock({ toggleSelection })
    const episodes = buildEpisodes(3)
    const { container } = render(<BatchGrid {...baseProps(episodes)} />)
    const cards = container.querySelectorAll('div.cursor-pointer')
    fireEvent.click(cards[1])
    expect(toggleSelection).toHaveBeenCalledWith(1)
  })

  it('shift-click with lastClickedIndex set calls selectRange', () => {
    const selectRange = vi.fn()
    setSelectionMock({ selectRange, lastClickedIndex: 0 })
    const episodes = buildEpisodes(3)
    const { container } = render(<BatchGrid {...baseProps(episodes)} />)
    const cards = container.querySelectorAll('div.cursor-pointer')
    fireEvent.click(cards[2], { shiftKey: true })
    expect(selectRange).toHaveBeenCalledWith(0, 2)
  })

  it('shift-click without lastClickedIndex falls back to toggleSelection', () => {
    const toggleSelection = vi.fn()
    const selectRange = vi.fn()
    setSelectionMock({ toggleSelection, selectRange, lastClickedIndex: null })
    const episodes = buildEpisodes(3)
    const { container } = render(<BatchGrid {...baseProps(episodes)} />)
    const cards = container.querySelectorAll('div.cursor-pointer')
    fireEvent.click(cards[1], { shiftKey: true })
    expect(toggleSelection).toHaveBeenCalledWith(1)
    expect(selectRange).not.toHaveBeenCalled()
  })

  it('handleBatchRate processes selected indices in chunks and clears selection', async () => {
    const clearSelection = vi.fn()
    const indices = Array.from({ length: 12 }, (_, i) => i)
    setSelectionMock({
      selectedCount: 12,
      selectedArray: indices,
      hasSelection: true,
      clearSelection,
    })
    const onBatchRate = vi.fn().mockResolvedValue(undefined)
    const episodes = buildEpisodes(15)
    render(<BatchGrid {...baseProps(episodes)} onBatchRate={onBatchRate} />)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Success/i }))
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Confirm$/ }))
    })
    expect(onBatchRate).toHaveBeenCalledTimes(2)
    expect(onBatchRate).toHaveBeenNthCalledWith(1, indices.slice(0, 10), 'success')
    expect(onBatchRate).toHaveBeenNthCalledWith(2, indices.slice(10), 'success')
    expect(clearSelection).toHaveBeenCalledTimes(1)
  })

  it('handleBatchQuality processes selected indices in chunks and clears selection', async () => {
    const clearSelection = vi.fn()
    const indices = [0, 1, 2, 3, 4]
    setSelectionMock({
      selectedCount: 5,
      selectedArray: indices,
      hasSelection: true,
      clearSelection,
    })
    const onBatchQuality = vi.fn().mockResolvedValue(undefined)
    const episodes = buildEpisodes(10)
    render(<BatchGrid {...baseProps(episodes)} onBatchQuality={onBatchQuality} />)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /4★/ }))
    })
    expect(onBatchQuality).toHaveBeenCalledTimes(1)
    expect(onBatchQuality).toHaveBeenCalledWith(indices, 4)
    expect(clearSelection).toHaveBeenCalledTimes(1)
  })

  it('handleBatchRate restores processing state even if onBatchRate rejects', async () => {
    const indices = [0, 1]
    setSelectionMock({
      selectedCount: 2,
      selectedArray: indices,
      hasSelection: true,
    })
    const onBatchRate = vi.fn().mockRejectedValue(new Error('boom'))
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const episodes = buildEpisodes(5)
    render(<BatchGrid {...baseProps(episodes)} onBatchRate={onBatchRate} />)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Failure/i }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(screen.queryByText('Processing batch...')).not.toBeInTheDocument()
    expect(consoleSpy).toHaveBeenCalledWith('Batch rating failed:', expect.any(Error))
    consoleSpy.mockRestore()
  })

  it('passes thumbnailUrl from getThumbnailUrl callback to episode cards', () => {
    const getThumbnailUrl = vi.fn(
      (_episode: EpisodeMeta, index: number) => `https://thumbs/${index}.jpg`,
    )
    const episodes = buildEpisodes(2)
    render(<BatchGrid {...baseProps(episodes)} getThumbnailUrl={getThumbnailUrl} />)
    expect(getThumbnailUrl).toHaveBeenCalledWith(episodes[0], 0)
    expect(getThumbnailUrl).toHaveBeenCalledWith(episodes[1], 1)
    const img0 = screen.getByRole('img', { name: /Episode 0/i })
    expect(img0).toHaveAttribute('src', 'https://thumbs/0.jpg')
  })

  it('marks selected episode cards based on selectedIndices', () => {
    setSelectionMock({ selectedIndices: new Set([1]) })
    const episodes = buildEpisodes(3)
    const { container } = render(<BatchGrid {...baseProps(episodes)} />)
    const ringCards = container.querySelectorAll('[class*="ring-primary"]')
    expect(ringCards.length).toBe(1)
  })
})
