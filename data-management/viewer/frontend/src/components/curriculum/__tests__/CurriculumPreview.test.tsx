import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import {
  CurriculumPreview,
  type EpisodePreviewItem,
} from '@/components/curriculum/CurriculumPreview'

const episode = (overrides: Partial<EpisodePreviewItem> = {}): EpisodePreviewItem => ({
  id: 'ep-1',
  episode_id: 'episode_001',
  has_anomalies: false,
  has_issues: false,
  ...overrides,
})

describe('CurriculumPreview', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders skeleton placeholders when loading', () => {
    const { container } = render(<CurriculumPreview episodes={[]} totalCount={0} isLoading />)

    expect(screen.getByText('Preview')).toBeInTheDocument()
    expect(container.querySelectorAll('.h-12.w-full')).toHaveLength(5)
    expect(screen.queryByText(/episodes$/)).not.toBeInTheDocument()
  })

  it('renders empty state when no episodes match', () => {
    render(<CurriculumPreview episodes={[]} totalCount={0} />)

    expect(screen.getByText(/No episodes match the current filters/i)).toBeInTheDocument()
    expect(screen.getByText('0 episodes')).toBeInTheDocument()
  })

  it('renders the formatted total count badge', () => {
    render(<CurriculumPreview episodes={[episode()]} totalCount={1500} />)

    expect(screen.getByText('1,500 episodes')).toBeInTheDocument()
  })

  it('renders each episode_id for displayed episodes', () => {
    const episodes = [
      episode({ id: 'a', episode_id: 'episode_aaa' }),
      episode({ id: 'b', episode_id: 'episode_bbb' }),
      episode({ id: 'c', episode_id: 'episode_ccc' }),
    ]

    render(<CurriculumPreview episodes={episodes} totalCount={3} />)

    expect(screen.getByText('episode_aaa')).toBeInTheDocument()
    expect(screen.getByText('episode_bbb')).toBeInTheDocument()
    expect(screen.getByText('episode_ccc')).toBeInTheDocument()
  })

  it('renders task completion rating when present', () => {
    render(<CurriculumPreview episodes={[episode({ task_completion_rating: 4 })]} totalCount={1} />)

    expect(screen.getByText('4')).toBeInTheDocument()
  })

  it('omits task completion rating display when absent', () => {
    const { container } = render(<CurriculumPreview episodes={[episode()]} totalCount={1} />)

    expect(container.querySelector('.fill-yellow-400')).toBeNull()
  })

  it('renders trajectory quality score when present', () => {
    render(
      <CurriculumPreview episodes={[episode({ trajectory_quality_score: 0.95 })]} totalCount={1} />,
    )

    expect(screen.getByText('0.95')).toBeInTheDocument()
  })

  it('omits trajectory quality score when absent', () => {
    const { container } = render(<CurriculumPreview episodes={[episode()]} totalCount={1} />)

    expect(container.querySelector('.text-green-500')).toBeNull()
  })

  it('renders thumbnail image when thumbnail_url provided', () => {
    render(
      <CurriculumPreview
        episodes={[episode({ thumbnail_url: 'https://example.com/thumb.jpg' })]}
        totalCount={1}
      />,
    )

    const img = document.querySelector('img')
    expect(img).not.toBeNull()
    expect(img?.getAttribute('src')).toBe('https://example.com/thumb.jpg')
  })

  it('renders placeholder icon when thumbnail_url is missing', () => {
    const { container } = render(<CurriculumPreview episodes={[episode()]} totalCount={1} />)

    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('.bg-muted.flex.h-10.w-14')).not.toBeNull()
  })

  it('renders Anomaly badge when has_anomalies is true', () => {
    render(<CurriculumPreview episodes={[episode({ has_anomalies: true })]} totalCount={1} />)

    expect(screen.getByText('Anomaly')).toBeInTheDocument()
  })

  it('does not render Anomaly badge when has_anomalies is false', () => {
    render(<CurriculumPreview episodes={[episode()]} totalCount={1} />)

    expect(screen.queryByText('Anomaly')).not.toBeInTheDocument()
  })

  it('renders Issue badge when has_issues is true', () => {
    render(<CurriculumPreview episodes={[episode({ has_issues: true })]} totalCount={1} />)

    expect(screen.getByText('Issue')).toBeInTheDocument()
  })

  it('does not render Issue badge when has_issues is false', () => {
    render(<CurriculumPreview episodes={[episode()]} totalCount={1} />)

    expect(screen.queryByText('Issue')).not.toBeInTheDocument()
  })

  it('renders "more episodes" footer when totalCount exceeds previewLimit', () => {
    const episodes = Array.from({ length: 50 }, (_, i) =>
      episode({ id: `e-${i}`, episode_id: `episode_${i}` }),
    )

    render(<CurriculumPreview episodes={episodes} totalCount={100} />)

    expect(screen.getByText(/\.\.\. and 50 more episodes/)).toBeInTheDocument()
  })

  it('omits "more episodes" footer when totalCount equals previewLimit', () => {
    const episodes = Array.from({ length: 3 }, (_, i) =>
      episode({ id: `e-${i}`, episode_id: `episode_${i}` }),
    )

    render(<CurriculumPreview episodes={episodes} totalCount={3} />)

    expect(screen.queryByText(/more episodes/)).not.toBeInTheDocument()
  })

  it('limits displayed episodes to previewLimit', () => {
    const episodes = Array.from({ length: 60 }, (_, i) =>
      episode({ id: `e-${i}`, episode_id: `episode_${i.toString().padStart(3, '0')}` }),
    )

    render(<CurriculumPreview episodes={episodes} totalCount={60} previewLimit={10} />)

    expect(screen.getByText('episode_000')).toBeInTheDocument()
    expect(screen.getByText('episode_009')).toBeInTheDocument()
    expect(screen.queryByText('episode_010')).not.toBeInTheDocument()
    expect(screen.queryByText('episode_059')).not.toBeInTheDocument()
  })

  it('shows custom previewLimit footer math', () => {
    const episodes = Array.from({ length: 5 }, (_, i) =>
      episode({ id: `e-${i}`, episode_id: `episode_${i}` }),
    )

    render(<CurriculumPreview episodes={episodes} totalCount={20} previewLimit={5} />)

    expect(screen.getByText(/\.\.\. and 15 more episodes/)).toBeInTheDocument()
  })

  it('applies the className prop to the root card', () => {
    const { container } = render(
      <CurriculumPreview episodes={[]} totalCount={0} className="custom-card-class" />,
    )

    expect(container.querySelector('.custom-card-class')).not.toBeNull()
  })

  it('applies the className prop while loading', () => {
    const { container } = render(
      <CurriculumPreview episodes={[]} totalCount={0} isLoading className="loading-card" />,
    )

    expect(container.querySelector('.loading-card')).not.toBeNull()
  })

  it('renders both rating and quality score together when both are present', () => {
    render(
      <CurriculumPreview
        episodes={[episode({ task_completion_rating: 5, trajectory_quality_score: 0.87 })]}
        totalCount={1}
      />,
    )

    expect(screen.getByText('5')).toBeInTheDocument()
    expect(screen.getByText('0.87')).toBeInTheDocument()
  })

  it('renders both Anomaly and Issue badges when both flags are true', () => {
    render(
      <CurriculumPreview
        episodes={[episode({ has_anomalies: true, has_issues: true })]}
        totalCount={1}
      />,
    )

    expect(screen.getByText('Anomaly')).toBeInTheDocument()
    expect(screen.getByText('Issue')).toBeInTheDocument()
  })

  it('renders multiple episodes with mixed metadata', () => {
    const episodes: EpisodePreviewItem[] = [
      episode({
        id: '1',
        episode_id: 'with_thumb',
        thumbnail_url: 'https://example.com/1.jpg',
        task_completion_rating: 3,
      }),
      episode({
        id: '2',
        episode_id: 'with_score',
        trajectory_quality_score: 0.5,
        has_anomalies: true,
      }),
      episode({ id: '3', episode_id: 'plain' }),
    ]

    render(<CurriculumPreview episodes={episodes} totalCount={3} />)

    const withThumb = screen.getByText('with_thumb').closest('div.flex.items-center')
    expect(withThumb).not.toBeNull()
    expect(within(withThumb as HTMLElement).getByText('3')).toBeInTheDocument()

    expect(screen.getByText('with_score')).toBeInTheDocument()
    expect(screen.getByText('0.5')).toBeInTheDocument()
    expect(screen.getByText('plain')).toBeInTheDocument()
    expect(screen.getByText('Anomaly')).toBeInTheDocument()
  })
})
