import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { EpisodePreviewCard } from '@/components/batch-annotation/EpisodePreviewCard'
import type { EpisodeMeta } from '@/types/api'

const mockEpisode = (overrides?: Partial<EpisodeMeta>): EpisodeMeta => ({
  index: 0,
  length: 100,
  taskIndex: 0,
  hasAnnotations: false,
  annotationStatus: 'pending',
  ...overrides,
})

const baseProps = () => ({
  episode: mockEpisode(),
  index: 5,
  isSelected: false,
  onToggleSelect: vi.fn(),
  onQuickRate: vi.fn(),
  onOpen: vi.fn(),
})

describe('EpisodePreviewCard', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('renders episode index and frame count', () => {
    render(<EpisodePreviewCard {...baseProps()} />)
    expect(screen.getByText('Episode 5')).toBeInTheDocument()
    expect(screen.getByText('100 frames')).toBeInTheDocument()
  })

  it('renders the optional task description when provided', () => {
    render(<EpisodePreviewCard {...baseProps()} episode={mockEpisode({ task: 'Pick up cube' })} />)
    expect(screen.getByText(/Pick up cube/)).toBeInTheDocument()
  })

  it('does not render a task description when episode.task is missing', () => {
    render(<EpisodePreviewCard {...baseProps()} />)
    expect(screen.queryByText(/Pick up/)).not.toBeInTheDocument()
  })

  it('renders thumbnail image when thumbnailUrl is provided', () => {
    render(<EpisodePreviewCard {...baseProps()} thumbnailUrl="https://example.com/thumb.jpg" />)
    const img = screen.getByRole('img', { name: /Episode 5/i })
    expect(img).toHaveAttribute('src', 'https://example.com/thumb.jpg')
  })

  it('renders Play placeholder when no thumbnail is provided', () => {
    const { container } = render(<EpisodePreviewCard {...baseProps()} />)
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(container.querySelector('svg.lucide-play')).toBeInTheDocument()
  })

  it('shows Check inside selection checkbox when isSelected is true', () => {
    const { container } = render(<EpisodePreviewCard {...baseProps()} isSelected />)
    const checkbox = container.querySelector('div.absolute.top-2.left-2') as HTMLElement | null
    expect(checkbox).not.toBeNull()
    expect(checkbox?.querySelector('svg.lucide-check')).not.toBeNull()
  })

  it('does not render Check inside selection checkbox when not selected', () => {
    const { container } = render(<EpisodePreviewCard {...baseProps()} />)
    const checkbox = container.querySelector('div.absolute.top-2.left-2') as HTMLElement | null
    expect(checkbox).not.toBeNull()
    expect(checkbox?.querySelector('svg.lucide-check')).toBeNull()
  })

  it('applies the selected ring classes when isSelected is true', () => {
    const { container } = render(<EpisodePreviewCard {...baseProps()} isSelected />)
    const card = container.querySelector('[class*="ring-primary"]')
    expect(card).not.toBeNull()
  })

  it('applies the annotated border class when annotationStatus is not pending', () => {
    const { container } = render(
      <EpisodePreviewCard
        {...baseProps()}
        episode={mockEpisode({ annotationStatus: 'complete' })}
      />,
    )
    expect(container.querySelector('[class*="border-green-200"]')).not.toBeNull()
  })

  it('does not render annotation status badge when status is pending', () => {
    render(<EpisodePreviewCard {...baseProps()} />)
    expect(screen.queryByText('pending')).not.toBeInTheDocument()
    expect(screen.queryByText('complete')).not.toBeInTheDocument()
    expect(screen.queryByText('in-progress')).not.toBeInTheDocument()
  })

  it('renders the complete badge with green styling', () => {
    render(
      <EpisodePreviewCard
        {...baseProps()}
        episode={mockEpisode({ annotationStatus: 'complete' })}
      />,
    )
    const badge = screen.getByText('complete')
    expect(badge.className).toContain('bg-green-100')
    expect(badge.className).toContain('text-green-700')
  })

  it('renders the in-progress badge with yellow styling', () => {
    render(
      <EpisodePreviewCard
        {...baseProps()}
        episode={mockEpisode({ annotationStatus: 'in-progress' })}
      />,
    )
    const badge = screen.getByText('in-progress')
    expect(badge.className).toContain('bg-yellow-100')
    expect(badge.className).toContain('text-yellow-700')
  })

  it('falls back to gray styling for unknown annotation status', () => {
    const { container } = render(
      <EpisodePreviewCard
        {...baseProps()}
        episode={mockEpisode({ annotationStatus: undefined })}
      />,
    )
    const grayBadge = container.querySelector('[class*="bg-gray-100"]')
    expect(grayBadge).not.toBeNull()
  })

  it('calls onToggleSelect when card is clicked', () => {
    const onToggleSelect = vi.fn()
    const { container } = render(
      <EpisodePreviewCard {...baseProps()} onToggleSelect={onToggleSelect} />,
    )
    const card = container.querySelector('div.cursor-pointer') as HTMLElement
    fireEvent.click(card)
    expect(onToggleSelect).toHaveBeenCalledWith(5, false)
  })

  it('passes shiftKey state to onToggleSelect', () => {
    const onToggleSelect = vi.fn()
    const { container } = render(
      <EpisodePreviewCard {...baseProps()} onToggleSelect={onToggleSelect} />,
    )
    const card = container.querySelector('div.cursor-pointer') as HTMLElement
    fireEvent.click(card, { shiftKey: true })
    expect(onToggleSelect).toHaveBeenCalledWith(5, true)
  })

  it('does not call onToggleSelect when click target is a button', () => {
    const onToggleSelect = vi.fn()
    const onQuickRate = vi.fn()
    render(
      <EpisodePreviewCard
        {...baseProps()}
        onToggleSelect={onToggleSelect}
        onQuickRate={onQuickRate}
      />,
    )
    const successButton = screen.getByRole('button', { name: /^S$/ })
    fireEvent.click(successButton)
    expect(onToggleSelect).not.toHaveBeenCalled()
    expect(onQuickRate).toHaveBeenCalledWith(5, 'success')
  })

  it('shows Open hover button after mouseEnter and hides on mouseLeave', () => {
    const { container } = render(<EpisodePreviewCard {...baseProps()} />)
    const card = container.querySelector('div.cursor-pointer') as HTMLElement

    expect(screen.queryByRole('button', { name: /Open/i })).not.toBeInTheDocument()

    fireEvent.mouseEnter(card)
    expect(screen.getByRole('button', { name: /Open/i })).toBeInTheDocument()

    fireEvent.mouseLeave(card)
    expect(screen.queryByRole('button', { name: /Open/i })).not.toBeInTheDocument()
  })

  it('calls onOpen with index when Open hover button is clicked', () => {
    const onOpen = vi.fn()
    const onToggleSelect = vi.fn()
    const { container } = render(
      <EpisodePreviewCard {...baseProps()} onOpen={onOpen} onToggleSelect={onToggleSelect} />,
    )
    const card = container.querySelector('div.cursor-pointer') as HTMLElement
    fireEvent.mouseEnter(card)
    const openButton = screen.getByRole('button', { name: /Open/i })
    fireEvent.click(openButton)
    expect(onOpen).toHaveBeenCalledWith(5)
    expect(onToggleSelect).not.toHaveBeenCalled()
  })

  it('calls onQuickRate with success when S button is clicked', () => {
    const onQuickRate = vi.fn()
    render(<EpisodePreviewCard {...baseProps()} onQuickRate={onQuickRate} />)
    fireEvent.click(screen.getByRole('button', { name: /^S$/ }))
    expect(onQuickRate).toHaveBeenCalledWith(5, 'success')
  })

  it('calls onQuickRate with partial when P button is clicked', () => {
    const onQuickRate = vi.fn()
    render(<EpisodePreviewCard {...baseProps()} onQuickRate={onQuickRate} />)
    fireEvent.click(screen.getByRole('button', { name: /^P$/ }))
    expect(onQuickRate).toHaveBeenCalledWith(5, 'partial')
  })

  it('calls onQuickRate with failure when F button is clicked', () => {
    const onQuickRate = vi.fn()
    render(<EpisodePreviewCard {...baseProps()} onQuickRate={onQuickRate} />)
    fireEvent.click(screen.getByRole('button', { name: /^F$/ }))
    expect(onQuickRate).toHaveBeenCalledWith(5, 'failure')
  })
})
