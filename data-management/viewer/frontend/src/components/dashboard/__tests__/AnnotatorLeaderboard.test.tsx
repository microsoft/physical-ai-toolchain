import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('date-fns', () => ({
  formatDistanceToNow: () => 'just now',
}))

import {
  type AnnotatorInfo,
  AnnotatorLeaderboard,
} from '@/components/dashboard/AnnotatorLeaderboard'

const annotators: AnnotatorInfo[] = [
  {
    annotatorId: 'u1',
    annotatorName: 'Alice Anderson',
    episodesAnnotated: 10,
    averageRating: 4.5,
    lastActive: '2025-01-01T00:00:00Z',
  },
  {
    annotatorId: 'u2',
    annotatorName: 'Bob Brown',
    episodesAnnotated: 30,
    averageRating: 4.2,
    lastActive: '2025-01-01T00:00:00Z',
  },
  {
    annotatorId: 'u3',
    annotatorName: 'Carol Clark',
    episodesAnnotated: 20,
    averageRating: 4.0,
    lastActive: '2025-01-01T00:00:00Z',
  },
  {
    annotatorId: 'u4',
    annotatorName: 'Dave',
    episodesAnnotated: 5,
    averageRating: 3.8,
    lastActive: '2025-01-01T00:00:00Z',
  },
]

describe('AnnotatorLeaderboard', () => {
  it('renders the empty state when no annotators are provided', () => {
    render(<AnnotatorLeaderboard annotators={[]} />)
    expect(screen.getByText('No annotator activity yet')).toBeInTheDocument()
  })

  it('sorts annotators by episodes annotated descending', () => {
    render(<AnnotatorLeaderboard annotators={annotators} />)
    const names = screen
      .getAllByText(/^(Alice Anderson|Bob Brown|Carol Clark|Dave)$/)
      .map((el) => el.textContent?.trim())
    expect(names).toEqual(['Bob Brown', 'Carol Clark', 'Alice Anderson', 'Dave'])
  })

  it('truncates the list to the limit prop', () => {
    render(<AnnotatorLeaderboard annotators={annotators} limit={2} />)
    expect(screen.getByText('Bob Brown')).toBeInTheDocument()
    expect(screen.getByText('Carol Clark')).toBeInTheDocument()
    expect(screen.queryByText('Alice Anderson')).not.toBeInTheDocument()
    expect(screen.queryByText('Dave')).not.toBeInTheDocument()
  })

  it('renders avatar initials from two-word and single-word names', () => {
    render(<AnnotatorLeaderboard annotators={annotators} />)
    expect(screen.getByText('BB')).toBeInTheDocument()
    expect(screen.getByText('CC')).toBeInTheDocument()
    expect(screen.getByText('AA')).toBeInTheDocument()
    expect(screen.getByText('DA')).toBeInTheDocument()
  })

  it('marks the top annotator with a "Top" badge', () => {
    render(<AnnotatorLeaderboard annotators={annotators} />)
    expect(screen.getByText('Top')).toBeInTheDocument()
  })
})
