import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const useDashboardMetricsMock = vi.fn()

vi.mock('@/hooks/use-dashboard', () => ({
  useDashboardMetrics: (datasetId: string) => useDashboardMetricsMock(datasetId),
}))

vi.mock('@/components/dashboard/ProgressOverview', () => ({
  ProgressOverview: () => <div data-testid="progress-overview" />,
}))

vi.mock('@/components/dashboard/RatingDistribution', () => ({
  RatingDistribution: ({ title }: { title: string }) => (
    <div data-testid="rating-distribution">{title}</div>
  ),
}))

vi.mock('@/components/dashboard/IssuesSummary', () => ({
  IssuesSummary: () => <div data-testid="issues-summary" />,
}))

vi.mock('@/components/dashboard/AnnotatorLeaderboard', () => ({
  AnnotatorLeaderboard: () => <div data-testid="annotator-leaderboard" />,
}))

vi.mock('@/components/dashboard/ActivityFeed', () => ({
  ActivityFeed: () => <div data-testid="activity-feed" />,
}))

import { QualityDashboard } from '@/components/dashboard/QualityDashboard'

const populatedReturn = {
  data: {
    total_episodes: 10,
    completion_rating_distribution: { '1': 0, '2': 1, '3': 2, '4': 3, '5': 4 },
    quality_rating_distribution: { '1': 0, '2': 0, '3': 1, '4': 4, '5': 5 },
    common_issues: [],
    anomalies: [],
    annotators: [],
    recent_activity: [],
  },
  metrics: { totalEpisodes: 10, annotatedEpisodes: 10, pendingEpisodes: 0, episodesPerHour: 0 },
  isLoading: false,
  error: null,
  refetch: vi.fn(),
}

beforeEach(() => {
  useDashboardMetricsMock.mockReset()
})

afterEach(() => {
  cleanup()
})

describe('QualityDashboard', () => {
  it('renders skeleton placeholders while loading', () => {
    useDashboardMetricsMock.mockReturnValue({
      data: undefined,
      metrics: undefined,
      isLoading: true,
      error: null,
      refetch: vi.fn(),
    })
    const { container } = render(<QualityDashboard datasetId="ds-1" />)

    const skeletons = container.querySelectorAll('.animate-pulse')
    expect(skeletons.length).toBeGreaterThanOrEqual(5)
  })

  it('renders error alert and wires retry button to refetch', async () => {
    const refetch = vi.fn()
    useDashboardMetricsMock.mockReturnValue({
      data: undefined,
      metrics: undefined,
      isLoading: false,
      error: new Error('boom'),
      refetch,
    })

    render(<QualityDashboard datasetId="ds-1" />)

    expect(screen.getByText('Failed to load dashboard')).toBeInTheDocument()
    expect(screen.getByText('Could not load dashboard statistics.')).toBeInTheDocument()

    const retry = screen.getByRole('button', { name: /retry/i })
    await userEvent.click(retry)
    expect(refetch).toHaveBeenCalledTimes(1)
  })

  it('renders the empty-state alert when no data is available', () => {
    useDashboardMetricsMock.mockReturnValue({
      data: undefined,
      metrics: undefined,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })

    render(<QualityDashboard datasetId="ds-1" />)
    expect(screen.getByText('No data available')).toBeInTheDocument()
    expect(
      screen.getByText('No annotation data is available for this dataset yet.'),
    ).toBeInTheDocument()
  })

  it('renders all child sections when data is populated', () => {
    useDashboardMetricsMock.mockReturnValue(populatedReturn)

    render(<QualityDashboard datasetId="ds-1" />)

    expect(screen.getByTestId('progress-overview')).toBeInTheDocument()
    const ratingCharts = screen.getAllByTestId('rating-distribution')
    expect(ratingCharts).toHaveLength(2)
    expect(screen.getByText('Task Completion Ratings')).toBeInTheDocument()
    expect(screen.getByText('Trajectory Quality')).toBeInTheDocument()
    expect(screen.getByTestId('issues-summary')).toBeInTheDocument()
    expect(screen.getByTestId('annotator-leaderboard')).toBeInTheDocument()
    expect(screen.getByTestId('activity-feed')).toBeInTheDocument()
  })

  it('passes the dataset id through to the metrics hook', () => {
    useDashboardMetricsMock.mockReturnValue(populatedReturn)
    render(<QualityDashboard datasetId="my-dataset-42" />)
    expect(useDashboardMetricsMock).toHaveBeenCalledWith('my-dataset-42')
  })
})
