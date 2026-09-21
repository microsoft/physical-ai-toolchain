import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { TrajectoryMetrics } from '@/hooks/use-ai-analysis'
import { useTrajectoryAnalysis } from '@/hooks/use-ai-analysis'

import { MotionMetricsPanel } from '../MotionMetricsPanel'

vi.mock('@/hooks/use-ai-analysis', () => ({
  useTrajectoryAnalysis: vi.fn(),
}))

const mockedUseTrajectoryAnalysis = vi.mocked(useTrajectoryAnalysis)

const buildMetrics = (overrides: Partial<TrajectoryMetrics> = {}): TrajectoryMetrics => ({
  smoothness: 0.000093,
  normalized_smoothness: 0.1988,
  efficiency: 0.0483,
  jitter: 0.0036,
  hesitation_count: 2,
  correction_count: 2,
  overall_score: 2,
  flags: ['jittery'],
  ...overrides,
})

const positions = [
  [0, 0, 0],
  [1, 1, 1],
  [2, 2, 2],
  [3, 3, 3],
]
const timestamps = [0, 0.033, 0.066, 0.099]

function mockHook(overrides: Partial<ReturnType<typeof useTrajectoryAnalysis>> = {}) {
  const refetch = vi.fn()
  mockedUseTrajectoryAnalysis.mockReturnValue({
    data: undefined,
    isFetching: false,
    error: null,
    refetch,
    ...overrides,
  } as unknown as ReturnType<typeof useTrajectoryAnalysis>)
  return refetch
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('MotionMetricsPanel', () => {
  it('shows a placeholder when there is not enough trajectory data', () => {
    mockHook()
    render(<MotionMetricsPanel datasetId="ds" episodeId="0" positions={[]} timestamps={[]} />)
    expect(screen.getByText(/no trajectory data/i)).toBeInTheDocument()
  })

  it('renders the computed motion metrics including normalized smoothness and flags', () => {
    mockHook({ data: buildMetrics() })
    render(
      <MotionMetricsPanel
        datasetId="ds"
        episodeId="0"
        positions={positions}
        timestamps={timestamps}
      />,
    )
    expect(screen.getByText(/normalized smoothness/i)).toBeInTheDocument()
    expect(screen.getByText('0.199')).toBeInTheDocument()
    expect(screen.getByText(/jittery/i)).toBeInTheDocument()
    expect(screen.getByTestId('motion-score')).toHaveTextContent('2')
  })

  it('requests analysis with the selected smoothness mode', () => {
    mockHook({ data: buildMetrics() })
    render(
      <MotionMetricsPanel
        datasetId="ds"
        episodeId="0"
        positions={positions}
        timestamps={timestamps}
      />,
    )

    // Default mode is log-scaled.
    expect(mockedUseTrajectoryAnalysis).toHaveBeenCalledWith(
      expect.objectContaining({
        trajectoryData: expect.objectContaining({ smoothness_mode: 'log-scaled' }),
      }),
    )

    fireEvent.click(screen.getByRole('button', { name: /radian-based/i }))

    expect(mockedUseTrajectoryAnalysis).toHaveBeenLastCalledWith(
      expect.objectContaining({
        trajectoryData: expect.objectContaining({ smoothness_mode: 'radian-based' }),
      }),
    )
  })

  it('refetches when the refresh control is pressed', () => {
    const refetch = mockHook({ data: buildMetrics() })
    render(
      <MotionMetricsPanel
        datasetId="ds"
        episodeId="0"
        positions={positions}
        timestamps={timestamps}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /refresh motion analysis/i }))
    expect(refetch).toHaveBeenCalledTimes(1)
  })
})
