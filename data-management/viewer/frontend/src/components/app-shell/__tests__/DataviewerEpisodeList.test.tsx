import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useLabelStore } from '@/stores/label-store'
import type { EpisodeMeta } from '@/types'

import { DataviewerEpisodeList } from '../DataviewerEpisodeList'

let mockEpisodesResult: {
  data: EpisodeMeta[] | undefined
  isLoading: boolean
  error: Error | null
} = { data: undefined, isLoading: false, error: null }

vi.mock('@/hooks/use-datasets', () => ({
  useEpisodes: () => mockEpisodesResult,
}))

vi.mock('@/components/annotation-panel', () => ({
  LabelFilter: () => <div data-testid="label-filter-stub" />,
}))

const sampleEpisodes: EpisodeMeta[] = [
  { index: 0, length: 120, taskIndex: 1, hasAnnotations: false },
  { index: 1, length: 80, taskIndex: 2, hasAnnotations: true },
  { index: 2, length: 200, taskIndex: 1, hasAnnotations: false },
]

const noopSelect = vi.fn()

describe('DataviewerEpisodeList', () => {
  beforeEach(() => {
    mockEpisodesResult = { data: undefined, isLoading: false, error: null }
    useLabelStore.getState().reset()
    noopSelect.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('shows the loading message while episodes are loading', () => {
    mockEpisodesResult = { data: undefined, isLoading: true, error: null }

    render(
      <DataviewerEpisodeList datasetId="ds-1" onSelectEpisode={noopSelect} selectedIndex={-1} />,
    )

    expect(screen.getByText(/loading episodes/i)).toBeInTheDocument()
  })

  it('surfaces the error message when the episodes query fails', () => {
    mockEpisodesResult = { data: undefined, isLoading: false, error: new Error('Boom') }

    render(
      <DataviewerEpisodeList datasetId="ds-1" onSelectEpisode={noopSelect} selectedIndex={-1} />,
    )

    expect(screen.getByText(/error: boom/i)).toBeInTheDocument()
  })

  it('renders the toolbar, total count, and one row per episode on success', () => {
    mockEpisodesResult = { data: sampleEpisodes, isLoading: false, error: null }

    render(
      <DataviewerEpisodeList datasetId="ds-1" onSelectEpisode={noopSelect} selectedIndex={1} />,
    )

    expect(screen.getByTestId('episode-list-toolbar')).toBeInTheDocument()
    expect(screen.getByTestId('label-filter-stub')).toBeInTheDocument()
    expect(screen.getByText(/^3\s*Episodes$/)).toBeInTheDocument()
    expect(screen.getByText('Episode 0')).toBeInTheDocument()
    expect(screen.getByText('Episode 1')).toBeInTheDocument()
    expect(screen.getByText('Episode 2')).toBeInTheDocument()
  })

  it('narrows visible episodes and shows fraction count when filter labels are active', () => {
    mockEpisodesResult = { data: sampleEpisodes, isLoading: false, error: null }
    useLabelStore.getState().setAllEpisodeLabels({ 0: ['SUCCESS'], 1: ['FAILURE'] })
    useLabelStore.getState().setFilterLabels(['SUCCESS'])

    render(
      <DataviewerEpisodeList datasetId="ds-1" onSelectEpisode={noopSelect} selectedIndex={-1} />,
    )

    expect(screen.getByText(/^1\s*\/\s*3\s*Episodes$/)).toBeInTheDocument()
    expect(screen.getByText('Episode 0')).toBeInTheDocument()
    expect(screen.queryByText('Episode 1')).not.toBeInTheDocument()
    expect(screen.queryByText('Episode 2')).not.toBeInTheDocument()
  })
})
