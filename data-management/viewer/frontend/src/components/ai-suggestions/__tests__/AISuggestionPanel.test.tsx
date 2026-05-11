import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AnnotationSuggestion, DetectedAnomaly } from '@/api/ai-analysis'
import {
  type SuggestAnnotationRequest,
  useAISuggestion,
  useRequestAISuggestion,
} from '@/hooks/use-ai-analysis'

import { AISuggestionPanel } from '../AISuggestionPanel'

vi.mock('@/hooks/use-ai-analysis', () => ({
  useAISuggestion: vi.fn(),
  useRequestAISuggestion: vi.fn(),
}))

vi.mock('../AISuggestionBadge', () => ({
  AISuggestionBadge: ({
    confidence,
    isAccepted,
    isLoading,
    hasError,
  }: {
    confidence?: number
    isAccepted?: boolean
    isLoading?: boolean
    hasError?: boolean
  }) => (
    <div
      data-testid="badge"
      data-confidence={confidence ?? ''}
      data-accepted={isAccepted ? 'true' : 'false'}
      data-loading={isLoading ? 'true' : 'false'}
      data-error={hasError ? 'true' : 'false'}
    />
  ),
}))

vi.mock('../SuggestionCard', () => ({
  SuggestionCard: ({
    isApplying,
    isAccepted,
    isRejected,
    onAccept,
    onReject,
    onPartialAccept,
  }: {
    isApplying?: boolean
    isAccepted?: boolean
    isRejected?: boolean
    onAccept?: () => void
    onReject?: () => void
    onPartialAccept?: (
      fields: Array<'task_completion' | 'trajectory_quality' | 'flags' | 'anomalies'>,
    ) => void
  }) => (
    <div
      data-testid="suggestion-card"
      data-applying={isApplying ? 'true' : 'false'}
      data-accepted={isAccepted ? 'true' : 'false'}
      data-rejected={isRejected ? 'true' : 'false'}
    >
      <button data-testid="card-accept" onClick={() => onAccept?.()}>
        accept
      </button>
      <button data-testid="card-reject" onClick={() => onReject?.()}>
        reject
      </button>
      <button data-testid="card-partial-flags" onClick={() => onPartialAccept?.(['flags'])}>
        partial-flags
      </button>
    </div>
  ),
}))

const mockedUseAISuggestion = vi.mocked(useAISuggestion)
const mockedUseRequestAISuggestion = vi.mocked(useRequestAISuggestion)

const buildAnomaly = (overrides: Partial<DetectedAnomaly> = {}): DetectedAnomaly => ({
  id: 'a-1',
  type: 'sudden_stop',
  severity: 'high',
  frame_start: 10,
  frame_end: 20,
  description: 'Trajectory stops abruptly',
  confidence: 0.95,
  auto_detected: true,
  ...overrides,
})

const buildSuggestion = (overrides: Partial<AnnotationSuggestion> = {}): AnnotationSuggestion => ({
  task_completion_rating: 4,
  trajectory_quality_score: 3,
  suggested_flags: ['needs_review', 'partial_success'],
  detected_anomalies: [buildAnomaly()],
  confidence: 0.82,
  reasoning: 'Trajectory shows smooth motion with one anomaly.',
  ...overrides,
})

const buildTrajectoryData = (): SuggestAnnotationRequest => ({
  positions: [
    [0, 0, 0],
    [1, 1, 1],
    [2, 2, 2],
  ],
  timestamps: [0, 0.1, 0.2],
})

type AISuggestionResult = ReturnType<typeof useAISuggestion>
type RequestAISuggestionResult = ReturnType<typeof useRequestAISuggestion>

const buildQueryResult = (overrides: Partial<AISuggestionResult> = {}): AISuggestionResult =>
  ({
    data: undefined,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  }) as AISuggestionResult

const buildMutationResult = (
  overrides: Partial<RequestAISuggestionResult> = {},
): RequestAISuggestionResult =>
  ({
    mutate: vi.fn(),
    isPending: false,
    ...overrides,
  }) as RequestAISuggestionResult

const getRefreshButton = (): HTMLButtonElement =>
  screen.getAllByRole('button').find((b) => b.className.includes('h-7')) as HTMLButtonElement

describe('AISuggestionPanel', () => {
  beforeEach(() => {
    mockedUseAISuggestion.mockReturnValue(buildQueryResult())
    mockedUseRequestAISuggestion.mockReturnValue(buildMutationResult())
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('shows fallback message when no trajectory data is provided', () => {
    render(<AISuggestionPanel datasetId="ds" episodeId="ep" />)
    expect(screen.getByText('No trajectory data available for analysis')).toBeInTheDocument()
  })

  it('renders skeleton placeholders and loading badge while loading', () => {
    mockedUseAISuggestion.mockReturnValue(buildQueryResult({ isLoading: true }))
    const { container } = render(
      <AISuggestionPanel datasetId="ds" episodeId="ep" trajectoryData={buildTrajectoryData()} />,
    )
    expect(screen.getByTestId('badge')).toHaveAttribute('data-loading', 'true')
    expect(container.querySelectorAll('.animate-pulse.rounded-md').length).toBeGreaterThanOrEqual(4)
  })

  it('shows error state and refresh triggers mutation', () => {
    const mutate = vi.fn()
    mockedUseAISuggestion.mockReturnValue(
      buildQueryResult({ error: new Error('boom') as unknown as null }),
    )
    mockedUseRequestAISuggestion.mockReturnValue(buildMutationResult({ mutate }))
    const trajectory = buildTrajectoryData()
    render(<AISuggestionPanel datasetId="ds" episodeId="ep" trajectoryData={trajectory} />)
    expect(screen.getByTestId('badge')).toHaveAttribute('data-error', 'true')
    fireEvent.click(screen.getByRole('button'))
    expect(mutate).toHaveBeenCalledWith(trajectory, expect.any(Object))
  })

  it('shows analyze button when no suggestion yet and triggers mutation on click', () => {
    const mutate = vi.fn()
    mockedUseRequestAISuggestion.mockReturnValue(buildMutationResult({ mutate }))
    const trajectory = buildTrajectoryData()
    render(<AISuggestionPanel datasetId="ds" episodeId="ep" trajectoryData={trajectory} />)
    fireEvent.click(screen.getByRole('button', { name: /analyze/i }))
    expect(mutate).toHaveBeenCalledWith(trajectory, expect.any(Object))
  })

  it('renders badge with confidence and SuggestionCard when suggestion is available', () => {
    const suggestion = buildSuggestion()
    mockedUseAISuggestion.mockReturnValue(buildQueryResult({ data: suggestion }))
    render(
      <AISuggestionPanel datasetId="ds" episodeId="ep" trajectoryData={buildTrajectoryData()} />,
    )
    expect(screen.getByTestId('badge')).toHaveAttribute('data-confidence', '0.82')
    expect(screen.getByTestId('suggestion-card')).toBeInTheDocument()
  })

  it('handleAccept invokes onApplySuggestion with all four fields and values', () => {
    const suggestion = buildSuggestion()
    mockedUseAISuggestion.mockReturnValue(buildQueryResult({ data: suggestion }))
    const onApplySuggestion = vi.fn()
    render(
      <AISuggestionPanel
        datasetId="ds"
        episodeId="ep"
        trajectoryData={buildTrajectoryData()}
        onApplySuggestion={onApplySuggestion}
      />,
    )
    fireEvent.click(screen.getByTestId('card-accept'))
    expect(onApplySuggestion).toHaveBeenCalledWith(
      ['task_completion', 'trajectory_quality', 'flags', 'anomalies'],
      {
        task_completion_rating: suggestion.task_completion_rating,
        trajectory_quality_score: suggestion.trajectory_quality_score,
        suggested_flags: suggestion.suggested_flags,
        detected_anomalies: suggestion.detected_anomalies,
      },
    )
    expect(screen.getByTestId('suggestion-card')).toHaveAttribute('data-accepted', 'true')
  })

  it('handleReject sets the card into rejected state', () => {
    mockedUseAISuggestion.mockReturnValue(buildQueryResult({ data: buildSuggestion() }))
    render(
      <AISuggestionPanel datasetId="ds" episodeId="ep" trajectoryData={buildTrajectoryData()} />,
    )
    fireEvent.click(screen.getByTestId('card-reject'))
    expect(screen.getByTestId('suggestion-card')).toHaveAttribute('data-rejected', 'true')
  })

  it('handlePartialAccept passes only requested field values', () => {
    const suggestion = buildSuggestion()
    mockedUseAISuggestion.mockReturnValue(buildQueryResult({ data: suggestion }))
    const onApplySuggestion = vi.fn()
    render(
      <AISuggestionPanel
        datasetId="ds"
        episodeId="ep"
        trajectoryData={buildTrajectoryData()}
        onApplySuggestion={onApplySuggestion}
      />,
    )
    fireEvent.click(screen.getByTestId('card-partial-flags'))
    expect(onApplySuggestion).toHaveBeenCalledWith(['flags'], {
      suggested_flags: suggestion.suggested_flags,
    })
    expect(screen.getByTestId('suggestion-card')).toHaveAttribute('data-accepted', 'true')
  })

  it('refresh onSuccess callback refetches and resets status to pending', () => {
    const refetch = vi.fn()
    const mutate = vi.fn(
      (_input: SuggestAnnotationRequest, options?: { onSuccess?: () => void }) => {
        options?.onSuccess?.()
      },
    ) as unknown as RequestAISuggestionResult['mutate']
    mockedUseAISuggestion.mockReturnValue(buildQueryResult({ data: buildSuggestion(), refetch }))
    mockedUseRequestAISuggestion.mockReturnValue(buildMutationResult({ mutate }))
    render(
      <AISuggestionPanel datasetId="ds" episodeId="ep" trajectoryData={buildTrajectoryData()} />,
    )
    fireEvent.click(getRefreshButton())
    expect(refetch).toHaveBeenCalledTimes(1)
  })

  it('disables refresh button and applies spin class while mutation is pending', () => {
    mockedUseAISuggestion.mockReturnValue(buildQueryResult({ data: buildSuggestion() }))
    mockedUseRequestAISuggestion.mockReturnValue(buildMutationResult({ isPending: true }))
    render(
      <AISuggestionPanel datasetId="ds" episodeId="ep" trajectoryData={buildTrajectoryData()} />,
    )
    const button = getRefreshButton()
    expect(button).toBeDisabled()
    expect(button.querySelector('.animate-spin')).not.toBeNull()
  })
})
