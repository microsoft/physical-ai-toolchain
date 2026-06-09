import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LanguageInstructionWidget } from '@/components/annotation-panel/LanguageInstructionWidget'
import { useAnnotationStore } from '@/stores/annotation-store'
import { useDatasetStore } from '@/stores/dataset-store'
import { useEpisodeStore } from '@/stores/episode-store'
import type { DatasetInfo, EpisodeAnnotation, EpisodeData } from '@/types'

vi.mock('@/hooks/use-annotations', () => ({
  useEpisodeAnnotations: () => ({ isLoading: false, data: undefined }),
}))

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

const datasetWithTask: DatasetInfo = {
  id: 'ds',
  name: 'Test Dataset',
  totalEpisodes: 1,
  fps: 30,
  features: {},
  tasks: [{ taskIndex: 0, description: 'pick the block' }],
}

const episode: EpisodeData = {
  meta: { index: 0, length: 10, taskIndex: 0, hasAnnotations: false },
  videoUrls: {},
  cameras: [],
  trajectoryData: [],
}

function seedStores({ withDatasetTask = true }: { withDatasetTask?: boolean } = {}) {
  useAnnotationStore.getState().clear()
  useDatasetStore.getState().reset()
  useEpisodeStore.getState().reset()

  if (withDatasetTask) {
    useDatasetStore.setState({ currentDataset: datasetWithTask })
  } else {
    useDatasetStore.setState({
      currentDataset: { ...datasetWithTask, tasks: [] },
    })
  }
  useEpisodeStore.getState().setCurrentEpisode(episode)
  useAnnotationStore.getState().loadAnnotation(baseAnnotation)
}

describe('LanguageInstructionWidget', () => {
  beforeEach(() => {
    seedStores()
  })

  afterEach(() => {
    useAnnotationStore.getState().clear()
    useDatasetStore.getState().reset()
    useEpisodeStore.getState().reset()
  })

  it('shows the empty state when no annotation is loaded', () => {
    useAnnotationStore.getState().clear()

    render(<LanguageInstructionWidget />)

    expect(screen.getByText('No episode selected')).toBeInTheDocument()
  })

  it('seeds the instruction from the dataset task description on Use as Instruction', async () => {
    const user = userEvent.setup()
    render(<LanguageInstructionWidget />)

    expect(screen.getByText('pick the block')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /use as instruction/i }))

    const langInst = useAnnotationStore.getState().currentAnnotation?.languageInstruction
    expect(langInst?.instruction).toBe('pick the block')
    expect(langInst?.source).toBe('template')
  })

  it('falls back to a blank instruction when no dataset task description exists', async () => {
    const user = userEvent.setup()
    seedStores({ withDatasetTask: false })

    render(<LanguageInstructionWidget />)

    await user.click(screen.getByRole('button', { name: /add instruction/i }))

    const langInst = useAnnotationStore.getState().currentAnnotation?.languageInstruction
    expect(langInst?.instruction).toBe('')
    expect(langInst?.source).toBe('human')
  })

  it('adds a paraphrase when Enter is pressed and clears the input', async () => {
    const user = userEvent.setup()
    useAnnotationStore.getState().updateLanguageInstruction({ instruction: 'lift the box' })

    render(<LanguageInstructionWidget />)

    const paraphraseInput = screen.getByPlaceholderText(/add alternative phrasing/i)
    await user.type(paraphraseInput, 'pick up the box{Enter}')

    const langInst = useAnnotationStore.getState().currentAnnotation?.languageInstruction
    expect(langInst?.paraphrases).toEqual(['pick up the box'])
    expect(paraphraseInput).toHaveValue('')
  })

  it('removes a paraphrase when its trash button is clicked', async () => {
    const user = userEvent.setup()
    useAnnotationStore.getState().updateLanguageInstruction({
      instruction: 'lift',
      paraphrases: ['raise', 'elevate'],
    })

    render(<LanguageInstructionWidget />)

    await user.click(screen.getByRole('button', { name: /remove paraphrase 1/i }))

    const langInst = useAnnotationStore.getState().currentAnnotation?.languageInstruction
    expect(langInst?.paraphrases).toEqual(['elevate'])
  })

  it('adds and removes subtask instructions', async () => {
    const user = userEvent.setup()
    useAnnotationStore.getState().updateLanguageInstruction({ instruction: 'lift' })

    render(<LanguageInstructionWidget />)

    const subtaskInput = screen.getByPlaceholderText(/add subtask step/i)
    await user.type(subtaskInput, 'approach{Enter}')
    await user.type(subtaskInput, 'grasp{Enter}')

    expect(
      useAnnotationStore.getState().currentAnnotation?.languageInstruction?.subtaskInstructions,
    ).toEqual(['approach', 'grasp'])

    await user.click(screen.getByRole('button', { name: /remove subtask 1/i }))

    expect(
      useAnnotationStore.getState().currentAnnotation?.languageInstruction?.subtaskInstructions,
    ).toEqual(['grasp'])
  })

  it('clears the language instruction via Remove Instruction', async () => {
    const user = userEvent.setup()
    useAnnotationStore.getState().updateLanguageInstruction({
      instruction: 'lift',
      paraphrases: ['raise'],
      subtaskInstructions: ['approach'],
    })

    render(<LanguageInstructionWidget />)

    await user.click(screen.getByRole('button', { name: /remove instruction/i }))

    expect(useAnnotationStore.getState().currentAnnotation?.languageInstruction).toBeUndefined()
  })

  it('ignores blank paraphrase submissions', async () => {
    const user = userEvent.setup()
    useAnnotationStore.getState().updateLanguageInstruction({ instruction: 'lift' })

    render(<LanguageInstructionWidget />)

    const paraphraseInput = screen.getByPlaceholderText(/add alternative phrasing/i)
    await user.type(paraphraseInput, '   {Enter}')

    expect(
      useAnnotationStore.getState().currentAnnotation?.languageInstruction?.paraphrases,
    ).toEqual([])
  })
})
