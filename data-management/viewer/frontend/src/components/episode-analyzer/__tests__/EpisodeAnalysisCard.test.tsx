import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useLabelStore } from '@/stores/label-store'

import { EpisodeAnalysisCard } from '../EpisodeAnalysisCard'

vi.mock('@/hooks/use-labels', () => ({
  useSavedEpisodeAnalysis: (index: number) => ({
    record: useLabelStore((state) => state.episodeAnalysis[index]),
  }),
}))

beforeEach(() => {
  useLabelStore.getState().reset()
})

afterEach(() => {
  useLabelStore.getState().reset()
})

describe('EpisodeAnalysisCard', () => {
  it('keeps motion-only evidence distinct from absent task findings without a duplicate title', () => {
    useLabelStore
      .getState()
      .setAllEpisodeAnalysis({ '0': { motionScore: 4, motionFlags: ['smooth'] } })
    render(<EpisodeAnalysisCard episodeIndex={0} />)
    expect(screen.getByRole('heading', { name: 'Motion analysis' })).toBeInTheDocument()
    expect(screen.getByText('No saved task-specific findings.')).toBeInTheDocument()
    expect(screen.queryByTestId('grasp-outcome')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Episode Analysis' })).not.toBeInTheDocument()
  })
  it('shows a placeholder when no analysis exists for the episode', () => {
    render(<EpisodeAnalysisCard episodeIndex={0} />)
    expect(screen.getByText(/no saved task-specific findings/i)).toBeInTheDocument()
  })

  it('renders the persisted VLM labels for the episode', () => {
    useLabelStore.getState().setAllEpisodeAnalysis({
      '0': {
        pickFrom: 'front',
        object: 'black cloth',
        graspSuccess: true,
        placeSuccess: false,
        movementQuality: 'Smooth and efficient.',
        notes: 'Gripper closed cleanly.',
        source: 'qwen3-vl',
        motionScore: 2,
        motionFlags: ['jittery'],
      },
    })

    render(<EpisodeAnalysisCard episodeIndex={0} />)

    expect(screen.getByText('black cloth')).toBeInTheDocument()
    expect(screen.getByText('front')).toBeInTheDocument()
    expect(screen.getByText('Smooth and efficient.')).toBeInTheDocument()
    expect(screen.getByText('Gripper closed cleanly.')).toBeInTheDocument()
    // Grasp succeeded, place failed -> both outcomes represented.
    expect(screen.getByTestId('grasp-outcome')).toHaveTextContent(/success/i)
    expect(screen.getByTestId('place-outcome')).toHaveTextContent(/fail/i)
  })
})
