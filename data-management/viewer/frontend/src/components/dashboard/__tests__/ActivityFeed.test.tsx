import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('date-fns', () => ({
  formatDistanceToNow: (date: Date) => {
    if (Number.isNaN(date.getTime())) {
      throw new Error('invalid date')
    }
    return `mocked-${date.toISOString()}`
  },
}))

import { ActivityFeed, type ActivityItem } from '@/components/dashboard/ActivityFeed'

const baseActivities: ActivityItem[] = [
  {
    id: 'a1',
    type: 'annotation',
    episode_id: 'ep-001',
    annotator_name: 'Alice',
    timestamp: '2025-01-01T10:00:00Z',
    summary: 'first',
  },
  {
    id: 'a2',
    type: 'review',
    episode_id: 'ep-002',
    annotator_name: 'Bob',
    timestamp: '2025-01-03T10:00:00Z',
    summary: 'second',
  },
  {
    id: 'a3',
    type: 'edit',
    episode_id: 'ep-003',
    annotator_name: 'Carol',
    timestamp: '2025-01-02T10:00:00Z',
    summary: 'third',
  },
]

describe('ActivityFeed', () => {
  it('renders the empty state when no activities are provided', () => {
    render(<ActivityFeed activities={[]} />)
    expect(screen.getByText('No recent activity')).toBeInTheDocument()
  })

  it('sorts activities by timestamp descending', () => {
    render(<ActivityFeed activities={baseActivities} />)
    const names = screen.getAllByText(/^(Alice|Bob|Carol)$/).map((el) => el.textContent?.trim())
    expect(names).toEqual(['Bob', 'Carol', 'Alice'])
  })

  it('renders the type badge label for each activity type', () => {
    render(<ActivityFeed activities={baseActivities} />)
    expect(screen.getByText('Annotated')).toBeInTheDocument()
    expect(screen.getByText('Reviewed')).toBeInTheDocument()
    expect(screen.getByText('Edited')).toBeInTheDocument()
  })

  it('renders the relative time string from formatDistanceToNow', () => {
    render(<ActivityFeed activities={[baseActivities[0]]} />)
    expect(screen.getByText('mocked-2025-01-01T10:00:00.000Z')).toBeInTheDocument()
  })

  it('falls back to "Unknown" when the timestamp is invalid', () => {
    render(
      <ActivityFeed activities={[{ ...baseActivities[0], id: 'bad', timestamp: 'not-a-date' }]} />,
    )
    expect(screen.getByText('Unknown')).toBeInTheDocument()
  })

  it('respects the limit prop', () => {
    render(<ActivityFeed activities={baseActivities} limit={2} />)
    expect(screen.getAllByText(/^(Alice|Bob|Carol)$/)).toHaveLength(2)
    expect(screen.getByText('Bob')).toBeInTheDocument()
    expect(screen.getByText('Carol')).toBeInTheDocument()
    expect(screen.queryByText('Alice')).not.toBeInTheDocument()
  })
})
