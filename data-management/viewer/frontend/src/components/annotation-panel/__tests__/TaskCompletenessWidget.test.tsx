import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { TaskCompletenessWidget } from '@/components/annotation-panel/TaskCompletenessWidget'
import { useAnnotationStore } from '@/stores/annotation-store'
import type { EpisodeAnnotation } from '@/types'

const baseAnnotation: EpisodeAnnotation = {
  annotatorId: 'tester',
  timestamp: '2026-01-01T00:00:00.000Z',
  taskCompleteness: { rating: 'unknown', confidence: 3 },
  trajectoryQuality: {
    overallScore: 3,
    metrics: { smoothness: 3, efficiency: 3, safety: 3, precision: 3 },
    flags: [],
  },
  dataQuality: { overallQuality: 'good', issues: [] },
  anomalies: { anomalies: [] },
}

function seed() {
  useAnnotationStore.getState().clear()
  useAnnotationStore.getState().loadAnnotation(baseAnnotation)
}

describe('TaskCompletenessWidget', () => {
  beforeEach(seed)
  afterEach(() => useAnnotationStore.getState().clear())

  it('shows the empty state when no annotation is loaded', () => {
    useAnnotationStore.getState().clear()
    render(<TaskCompletenessWidget />)
    expect(screen.getByText(/no episode selected/i)).toBeInTheDocument()
  })

  it('sets the rating when a button is clicked', async () => {
    const user = userEvent.setup()
    render(<TaskCompletenessWidget />)

    await user.click(screen.getByRole('button', { name: /success/i }))

    expect(useAnnotationStore.getState().currentAnnotation?.taskCompleteness?.rating).toBe(
      'success',
    )
  })

  it('responds to keyboard shortcuts for rating', () => {
    render(<TaskCompletenessWidget />)

    fireEvent.keyDown(window, { key: 'f' })
    expect(useAnnotationStore.getState().currentAnnotation?.taskCompleteness?.rating).toBe(
      'failure',
    )

    fireEvent.keyDown(window, { key: 'p' })
    expect(useAnnotationStore.getState().currentAnnotation?.taskCompleteness?.rating).toBe(
      'partial',
    )
  })

  it('updates the confidence level via the slider', () => {
    render(<TaskCompletenessWidget />)
    const slider = screen.getByRole('slider')

    fireEvent.change(slider, { target: { value: '5' } })

    expect(useAnnotationStore.getState().currentAnnotation?.taskCompleteness?.confidence).toBe(5)
  })

  it('reveals partial-completion controls when the rating is partial', () => {
    useAnnotationStore.getState().updateTaskCompleteness({ rating: 'partial' })
    render(<TaskCompletenessWidget />)

    expect(screen.getByText(/completion:/i)).toBeInTheDocument()
    expect(screen.getByText(/last subtask reached/i)).toBeInTheDocument()
  })
})
