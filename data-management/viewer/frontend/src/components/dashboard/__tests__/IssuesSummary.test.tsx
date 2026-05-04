import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { type IssueItem, IssuesSummary } from '@/components/dashboard/IssuesSummary'

const issues: IssueItem[] = [
  { name: 'gripper_slip', count: 6 },
  { name: 'collision_detected', count: 4 },
  { name: 'low_lighting', count: 3 },
  { name: 'occluded_view', count: 2 },
  { name: 'noisy_audio', count: 1 },
  { name: 'overflow_issue', count: 1 },
]

const anomalies: IssueItem[] = [
  { name: 'unexpected_motion', count: 2 },
  { name: 'sensor_glitch', count: 1 },
]

afterEach(() => {
  cleanup()
})

describe('IssuesSummary', () => {
  it('renders the empty state when no issues or anomalies are provided', () => {
    render(<IssuesSummary issues={[]} anomalies={[]} />)
    expect(screen.getByText('No issues or anomalies detected yet')).toBeInTheDocument()
  })

  it('renders summary badges with totals for issues and anomalies', () => {
    render(<IssuesSummary issues={issues} anomalies={anomalies} />)
    expect(screen.getByText('17 Issues')).toBeInTheDocument()
    expect(screen.getByText('3 Anomalies')).toBeInTheDocument()
  })

  it('formats snake_case names to Title Case', () => {
    render(<IssuesSummary issues={issues} anomalies={anomalies} />)
    expect(screen.getByText('Gripper Slip')).toBeInTheDocument()
    expect(screen.getByText('Collision Detected')).toBeInTheDocument()
    expect(screen.getByText('Unexpected Motion')).toBeInTheDocument()
  })

  it('shows percentages when totalEpisodes > 0 and hides them otherwise', () => {
    const { unmount } = render(
      <IssuesSummary issues={issues} anomalies={anomalies} totalEpisodes={20} />,
    )
    // 6 / 20 = 30%
    expect(screen.getByText(/\(30%\)/)).toBeInTheDocument()
    unmount()

    render(<IssuesSummary issues={issues} anomalies={anomalies} totalEpisodes={0} />)
    expect(screen.queryByText(/\(\d+%\)/)).not.toBeInTheDocument()
  })

  it('truncates issues and anomalies to the top 5 entries', () => {
    render(<IssuesSummary issues={issues} anomalies={anomalies} />)
    expect(screen.getByText('Noisy Audio')).toBeInTheDocument()
    expect(screen.queryByText('Overflow Issue')).not.toBeInTheDocument()
  })
})
