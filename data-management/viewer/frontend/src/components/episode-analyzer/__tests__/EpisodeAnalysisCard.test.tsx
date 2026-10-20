import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useLabelStore } from '@/stores/label-store'
import type { ContributionLedger } from '@/types/annotations'

import { EpisodeAnalysisCard } from '../EpisodeAnalysisCard'

let savedState: {
  ledger?: ContributionLedger
  error?: Error
  isLoading?: boolean
  replaced?: boolean
} = {}
const refetch = vi.fn()

vi.mock('@/hooks/use-labels', () => ({
  useSavedEpisodeAnalysis: (index: number) => ({
    record: useLabelStore((state) => state.episodeAnalysis[index]),
    ...savedState,
    refetch,
  }),
}))

beforeEach(() => {
  useLabelStore.getState().reset()
  savedState = {}
  refetch.mockReset()
})

afterEach(() => {
  useLabelStore.getState().reset()
})

describe('EpisodeAnalysisCard', () => {
  it('retains saved findings during refresh failure and retries without changing evidence', async () => {
    const user = userEvent.setup()
    savedState = { error: new Error('private error'), isLoading: true, replaced: true }
    useLabelStore.getState().setAllEpisodeAnalysis({
      '0': { object: 'cube', smoothness: 0.8, efficiency: 0.7, jitter: 0.1 },
    })
    render(<EpisodeAnalysisCard episodeIndex={0} />)
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Previously loaded analysis remains visible',
    )
    expect(screen.getByText('Loading saved analysis...')).toBeVisible()
    expect(
      screen.getByText('Machine findings from another source revision are excluded.'),
    ).toBeVisible()
    expect(screen.getByText('cube')).toBeVisible()
    expect(screen.getByTestId('grasp-outcome')).toHaveTextContent('Unknown')
    expect(screen.getByText('Smoothness')).toBeVisible()
    expect(screen.getByText('Efficiency')).toBeVisible()
    expect(screen.getByText('Jitter')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Retry analysis' }))
    expect(refetch).toHaveBeenCalledOnce()
    expect(useLabelStore.getState().episodeAnalysis[0]?.object).toBe('cube')
    expect(screen.queryByText('private error')).not.toBeInTheDocument()
  })

  it('distinguishes active machine authorship and acceptance from withdrawn or label contributions', () => {
    const contribution = {
      id: 'active',
      field: 'analysis/object',
      origin: 'machine' as const,
      value: 'cube',
      sequence: 1,
      timestamp: '2026-10-01T00:00:00Z',
      authorId: 'actor',
      machine: null,
    }
    savedState.ledger = {
      schemaVersion: '1.0.0',
      contributions: [
        contribution,
        { ...contribution, id: 'withdrawn', origin: 'human' },
        { ...contribution, id: 'label', field: 'labels/SUCCESS', origin: 'template' },
      ],
      acceptances: { active: ['reviewer'] },
      withdrawn: ['withdrawn'],
    }
    useLabelStore.getState().setAllEpisodeAnalysis({ '0': { object: 'cube' } })
    render(<EpisodeAnalysisCard episodeIndex={0} />)
    expect(screen.getByText(/Origin: machine/)).toHaveTextContent(
      'accepted without changing authorship',
    )
    expect(screen.getByText(/Origin: machine/)).not.toHaveTextContent('human')
  })

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
