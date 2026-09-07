import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { LabelPanel } from '@/components/annotation-panel/LabelPanel'
import { useLabelStore } from '@/stores/label-store'

const mockToggle = vi.fn()
const mockAddLabelOption = vi.fn()
const mockRemoveLabelOption = vi.fn()
const mockImportAnalysisLabels = vi.fn()
let mockCurrentLabels = ['SUCCESS']

vi.mock('@/hooks/use-labels', () => ({
  IMPORTABLE_ANALYSIS_FIELDS: [
    'object',
    'pick_from',
    'grasp_success',
    'place_success',
    'motion_score',
    'motion_flags',
    'source',
  ],
  useCurrentEpisodeLabels: () => ({
    currentLabels: mockCurrentLabels,
    toggle: mockToggle,
  }),
  useAddLabelOption: () => ({
    mutateAsync: mockAddLabelOption,
    isPending: false,
  }),
  useRemoveLabelOption: () => ({
    mutateAsync: mockRemoveLabelOption,
    isPending: false,
  }),
  useImportAnalysisLabels: () => ({
    mutateAsync: mockImportAnalysisLabels,
    isPending: false,
  }),
}))

describe('LabelPanel', () => {
  beforeEach(() => {
    mockToggle.mockReset()
    mockAddLabelOption.mockReset()
    mockRemoveLabelOption.mockReset()
    mockImportAnalysisLabels.mockReset()
    mockToggle.mockResolvedValue(undefined)
    mockAddLabelOption.mockResolvedValue(undefined)
    mockRemoveLabelOption.mockResolvedValue(undefined)
    mockImportAnalysisLabels.mockResolvedValue({
      labels_added: [],
      episodes_updated: 0,
    })
    mockCurrentLabels = ['SUCCESS']
    useLabelStore.getState().reset()
    useLabelStore.getState().setAvailableLabels(['SUCCESS', 'FAILURE', 'REVIEW'])
  })

  it('does not show save-all or inline autosave copy in the label panel', () => {
    render(<LabelPanel episodeIndex={3} />)

    expect(screen.queryByRole('button', { name: /save all/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/changes save automatically/i)).not.toBeInTheDocument()
  })

  it('protects built-in labels from deletion', () => {
    render(<LabelPanel episodeIndex={3} />)

    expect(screen.queryByRole('button', { name: /delete label success/i })).not.toBeInTheDocument()
    expect(screen.getByText(/built-in labels stay available/i)).toBeInTheDocument()
  })

  it('asks for confirmation before deleting a custom label', async () => {
    const user = userEvent.setup()

    render(<LabelPanel episodeIndex={3} />)

    await user.click(screen.getByRole('button', { name: /delete label review/i }))

    expect(screen.getByRole('dialog', { name: /delete label/i })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /delete label/i }))

    expect(mockRemoveLabelOption).toHaveBeenCalledWith('REVIEW')
  })

  it('does not show success feedback or undo after a label change', async () => {
    const user = userEvent.setup()

    render(<LabelPanel episodeIndex={3} />)

    await user.click(screen.getByRole('button', { name: 'SUCCESS' }))

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /undo/i })).not.toBeInTheDocument()
    expect(mockToggle).toHaveBeenCalledTimes(1)
    expect(mockToggle).toHaveBeenCalledWith('SUCCESS')
  })

  it('shows a compact error message when a label change fails', async () => {
    const user = userEvent.setup()
    mockToggle.mockRejectedValueOnce(new Error('network down'))

    render(<LabelPanel episodeIndex={3} />)

    await user.click(screen.getByRole('button', { name: 'SUCCESS' }))

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText(/failed to update labels/i)).toBeInTheDocument()
    expect(screen.getByText(/network down/i)).toBeInTheDocument()
  })

  it('imports detected analysis fields with custom prefix and overwrite enabled', async () => {
    const user = userEvent.setup()
    useLabelStore.getState().setAllEpisodeAnalysis({
      '3': { object: 'red cube', grasp_success: true },
    })
    mockImportAnalysisLabels.mockResolvedValueOnce({
      labels_added: ['ITEM: RED CUBE'],
      episodes_updated: 1,
    })

    render(<LabelPanel episodeIndex={3} />)

    expect(screen.getByRole('button', { name: /^object$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^grasp$/i })).toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: /label prefix/i }), 'ITEM')
    await user.click(screen.getByRole('checkbox', { name: /replace existing imported labels/i }))
    await user.click(screen.getByRole('button', { name: /^object$/i }))

    expect(mockImportAnalysisLabels).toHaveBeenCalledWith({
      field: 'object',
      prefix: 'ITEM',
      overwrite: true,
    })
    expect(screen.getByText(/imported 1 object label across 1 episode/i)).toBeInTheDocument()
  })

  it('shows an import error without reporting success', async () => {
    const user = userEvent.setup()
    useLabelStore.getState().setAllEpisodeAnalysis({ '3': { object: 'red cube' } })
    mockImportAnalysisLabels.mockRejectedValueOnce(new Error('server unavailable'))

    render(<LabelPanel episodeIndex={3} />)

    await user.click(screen.getByRole('button', { name: /^object$/i }))

    expect(screen.getByText(/failed to import labels/i)).toBeInTheDocument()
    expect(screen.getByText(/server unavailable/i)).toBeInTheDocument()
    expect(screen.queryByText(/^imported/i)).not.toBeInTheDocument()
  })
})
