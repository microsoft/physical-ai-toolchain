import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useLabelStore } from '@/stores/label-store'

import { EpisodeAnalysisCard } from '../EpisodeAnalysisCard'

beforeEach(() => {
  useLabelStore.getState().reset()
})

afterEach(() => {
  useLabelStore.getState().reset()
})

describe('EpisodeAnalysisCard', () => {
  it('shows a placeholder when no analysis exists for the episode', () => {
    render(<EpisodeAnalysisCard episodeIndex={0} />)
    expect(screen.getByText(/no saved analysis/i)).toBeInTheDocument()
  })

  it('renders the persisted VLM labels for the episode', () => {
    useLabelStore.getState().setAllEpisodeAnalysis({
      '0': {
        pick_from: 'front',
        object: 'black cloth',
        grasp_success: true,
        place_success: false,
        movement_quality: 'Smooth and efficient.',
        notes: 'Gripper closed cleanly.',
        source: 'qwen3-vl',
        motion_score: 2,
        motion_flags: ['jittery'],
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
