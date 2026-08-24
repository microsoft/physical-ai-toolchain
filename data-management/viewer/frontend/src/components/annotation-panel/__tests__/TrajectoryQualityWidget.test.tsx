import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { TrajectoryQualityWidget } from '@/components/annotation-panel/TrajectoryQualityWidget'
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

describe('TrajectoryQualityWidget', () => {
  beforeEach(seed)
  afterEach(() => useAnnotationStore.getState().clear())

  it('shows the empty state when no annotation is loaded', () => {
    useAnnotationStore.getState().clear()
    render(<TrajectoryQualityWidget />)
    expect(screen.getByText(/no episode selected/i)).toBeInTheDocument()
  })

  it('renders metric ratings and flag toggles', () => {
    render(<TrajectoryQualityWidget />)
    expect(screen.getByText('Smoothness')).toBeInTheDocument()
    expect(screen.getByText('Precision')).toBeInTheDocument()
    expect(screen.getByText('Jittery')).toBeInTheDocument()
  })

  it('sets the overall score from a number key shortcut', () => {
    render(<TrajectoryQualityWidget />)

    fireEvent.keyDown(window, { key: '4' })

    expect(useAnnotationStore.getState().currentAnnotation?.trajectoryQuality?.overallScore).toBe(4)
  })

  it('toggles the jittery flag from the keyboard shortcut', () => {
    render(<TrajectoryQualityWidget />)

    fireEvent.keyDown(window, { key: 'j' })
    expect(useAnnotationStore.getState().currentAnnotation?.trajectoryQuality?.flags).toContain(
      'jittery',
    )

    fireEvent.keyDown(window, { key: 'j' })
    expect(useAnnotationStore.getState().currentAnnotation?.trajectoryQuality?.flags).not.toContain(
      'jittery',
    )
  })

  it('toggles a flag when its control is clicked', () => {
    render(<TrajectoryQualityWidget />)

    fireEvent.click(screen.getByText('Inefficient'))

    expect(useAnnotationStore.getState().currentAnnotation?.trajectoryQuality?.flags).toContain(
      'inefficient-path',
    )
  })
})
