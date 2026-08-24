import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { DataQualityWidget } from '@/components/annotation-panel/DataQualityWidget'
import { useAnnotationStore } from '@/stores/annotation-store'
import { useEpisodeStore } from '@/stores/episode-store'
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
  useEpisodeStore.getState().reset()
  useAnnotationStore.getState().clear()
  useAnnotationStore.getState().loadAnnotation(baseAnnotation)
}

describe('DataQualityWidget', () => {
  beforeEach(seed)
  afterEach(() => {
    useAnnotationStore.getState().clear()
    useEpisodeStore.getState().reset()
  })

  it('shows the empty state when no annotation is loaded', () => {
    useAnnotationStore.getState().clear()
    render(<DataQualityWidget />)
    expect(screen.getByText(/no episode selected/i)).toBeInTheDocument()
  })

  it('renders the current overall quality and the rating options', () => {
    render(<DataQualityWidget />)
    expect(screen.getByRole('button', { name: 'Good' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Unusable' })).toBeInTheDocument()
  })

  it('updates the overall quality when a rating is selected', async () => {
    const user = userEvent.setup()
    render(<DataQualityWidget />)

    await user.click(screen.getByRole('button', { name: 'Poor' }))

    expect(useAnnotationStore.getState().currentAnnotation?.dataQuality?.overallQuality).toBe(
      'poor',
    )
  })

  it('opens the add-issue dialog and reflects the issue count', () => {
    useAnnotationStore.getState().updateDataQuality({
      issues: [{ type: 'occlusion', severity: 'minor' }],
    })
    render(<DataQualityWidget />)

    expect(screen.getByText(/issues \(1\)/i)).toBeInTheDocument()

    // Opening the dialog must not throw and keeps the widget mounted.
    fireEvent.click(screen.getByRole('button', { name: /add issue/i }))
    expect(screen.getByText('Data Quality')).toBeInTheDocument()
  })
})
