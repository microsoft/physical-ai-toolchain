import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('date-fns', () => ({
  formatDistanceToNow: () => 'just now',
}))

import {
  type AnnotatorInfo,
  AnnotatorLeaderboard,
} from '@/components/dashboard/AnnotatorLeaderboard'

const annotators: AnnotatorInfo[] = [
  {
    annotator_id: 'u1',
    annotator_name: 'Alice Anderson',
    episodes_annotated: 10,
    average_rating: 4.5,
    last_active: '2025-01-01T00:00:00Z',
  },
  {
    annotator_id: 'u2',
    annotator_name: 'Bob Brown',
    episodes_annotated: 30,
    average_rating: 4.2,
    last_active: '2025-01-01T00:00:00Z',
  },
  {
    annotator_id: 'u3',
    annotator_name: 'Carol Clark',
    episodes_annotated: 20,
    average_rating: 4.0,
    last_active: '2025-01-01T00:00:00Z',
  },
  {
    annotator_id: 'u4',
    annotator_name: 'Dave',
    episodes_annotated: 5,
    average_rating: 3.8,
    last_active: '2025-01-01T00:00:00Z',
  },
]

afterEach(() => {
  cleanup()
})

describe('AnnotatorLeaderboard', () => {
  it('renders the empty state when no annotators are provided', () => {
    render(<AnnotatorLeaderboard annotators={[]} />)
    expect(screen.getByText('No annotator activity yet')).toBeInTheDocument()
  })

  it('sorts annotators by episodes_annotated descending', () => {
    const { container } = render(<AnnotatorLeaderboard annotators={annotators} />)
    const names = Array.from(container.querySelectorAll('span.truncate.font-medium')).map((el) =>
      el.textContent?.trim(),
    )
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
