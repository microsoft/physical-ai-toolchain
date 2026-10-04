import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ExportDialog } from '@/components/export/ExportDialog'
import { useCapabilities } from '@/hooks/use-datasets'
import { useExport } from '@/hooks/use-export'
import { getEffectiveFrameCount, useEditStore, useEpisodeStore } from '@/stores'
import { renderWithQuery } from '@/test-utils/render'

vi.mock('@/hooks/use-export', () => ({
  useExport: vi.fn(),
}))

vi.mock('@/hooks/use-datasets', () => ({
  useCapabilities: vi.fn(),
}))

vi.mock('@/stores', () => ({
  useEditStore: vi.fn(),
  useEpisodeStore: vi.fn(),
  getEffectiveFrameCount: vi.fn(),
}))

interface MockEditState {
  getEditOperations: () => unknown
  removedFrames: Set<number>
  insertedFrames: Set<number>
}

interface MockEpisodeState {
  currentEpisode: { meta: { length: number } } | null
}

function createUseExportReturn(overrides: Partial<ReturnType<typeof useExport>> = {}) {
  return {
    isExporting: false,
    progress: null,
    result: null,
    error: null,
    previewStats: null,
    isLoadingPreview: false,
    startExport: vi.fn(),
    cancelExport: vi.fn(),
    fetchPreview: vi.fn(),
    reset: vi.fn(),
    ...overrides,
  } as ReturnType<typeof useExport>
}

function mockCapabilities(isLerobotDataset: boolean | undefined) {
  vi.mocked(useCapabilities).mockReturnValue({
    data: isLerobotDataset === undefined ? undefined : { isLerobotDataset },
  } as ReturnType<typeof useCapabilities>)
}

describe('ExportDialog', () => {
  let editState: MockEditState
  let episodeState: MockEpisodeState

  beforeEach(() => {
    editState = {
      getEditOperations: vi.fn(() => null),
      removedFrames: new Set<number>(),
      insertedFrames: new Set<number>(),
    }
    episodeState = {
      currentEpisode: { meta: { length: 100 } },
    }

    vi.mocked(useEditStore).mockImplementation((selector: unknown) =>
      (selector as (state: MockEditState) => unknown)(editState),
    )
    vi.mocked(useEpisodeStore).mockImplementation((selector: unknown) =>
      (selector as (state: MockEpisodeState) => unknown)(episodeState),
    )
    vi.mocked(getEffectiveFrameCount).mockReturnValue(100)
    vi.mocked(useExport).mockReturnValue(createUseExportReturn())
    mockCapabilities(undefined)
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('does not render dialog content when closed', () => {
    renderWithQuery(
      <ExportDialog
        open={false}
        onOpenChange={vi.fn()}
        datasetId="dataset-1"
        episodeIndices={[0, 1]}
      />,
    )

    expect(screen.queryByText('Export Episodes')).toBeNull()
  })

  it('renders title, description, and action buttons when open', () => {
    renderWithQuery(
      <ExportDialog open onOpenChange={vi.fn()} datasetId="dataset-1" episodeIndices={[0, 1]} />,
    )

    expect(screen.getByText('Export Episodes')).toBeInTheDocument()
    expect(screen.getByText(/Export 2 episode\(s\) with applied edits\./i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start export/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^cancel$/i })).toBeInTheDocument()
  })

  it('invokes startExport with the constructed request when Start Export is clicked', async () => {
    const user = userEvent.setup()
    const startExport = vi.fn()
    vi.mocked(useExport).mockReturnValue(createUseExportReturn({ startExport }))

    renderWithQuery(
      <ExportDialog open onOpenChange={vi.fn()} datasetId="dataset-1" episodeIndices={[0, 1]} />,
    )

    await user.click(screen.getByRole('button', { name: /start export/i }))

    expect(startExport).toHaveBeenCalledTimes(1)
    expect(startExport).toHaveBeenCalledWith(
      expect.objectContaining({
        episodeIndices: [0, 1],
        outputPath: '/exports',
        applyEdits: true,
      }),
    )
    expect(startExport.mock.calls[0][0]).not.toHaveProperty('format')
    expect(startExport.mock.calls[0][0]).not.toHaveProperty('includeSubtasks')
    expect(startExport.mock.calls[0][0]).not.toHaveProperty('includeLanguageInstructions')
  })

  it('says a LeRobot source exports a new LeRobot dataset', () => {
    mockCapabilities(true)

    renderWithQuery(
      <ExportDialog open onOpenChange={vi.fn()} datasetId="dataset-1" episodeIndices={[0]} />,
    )

    expect(screen.getByText(/as a new LeRobot dataset/i)).toBeInTheDocument()
    expect(screen.getByText(/new or empty output directory/i)).toBeInTheDocument()
  })

  it('says an HDF5 source exports HDF5 episode files', () => {
    mockCapabilities(false)

    renderWithQuery(
      <ExportDialog open onOpenChange={vi.fn()} datasetId="dataset-1" episodeIndices={[0]} />,
    )

    expect(screen.getByText(/to HDF5 episode files/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/include subtask metadata/i)).toBeInTheDocument()
    expect(screen.queryByLabelText(/include language instructions/i)).toBeNull()
  })

  it('sends the language option for LeRobot sources, checked by default', async () => {
    mockCapabilities(true)
    const user = userEvent.setup()
    const startExport = vi.fn()
    vi.mocked(useExport).mockReturnValue(createUseExportReturn({ startExport }))

    renderWithQuery(
      <ExportDialog open onOpenChange={vi.fn()} datasetId="dataset-1" episodeIndices={[0]} />,
    )
    const option = screen.getByLabelText(
      /include language instructions as LeRobot task phrasings and plan/i,
    )
    expect(option).toBeChecked()
    await user.click(screen.getByRole('button', { name: /start export/i }))
    await user.click(option)
    await user.click(screen.getByRole('button', { name: /start export/i }))

    expect(startExport.mock.calls.map((call) => call[0].includeLanguageInstructions)).toEqual([
      true,
      false,
    ])
  })

  it('says a LeRobot export writes subtasks as LeRobot subtask annotations', async () => {
    mockCapabilities(true)
    const user = userEvent.setup()
    const startExport = vi.fn()
    vi.mocked(useExport).mockReturnValue(createUseExportReturn({ startExport }))
    editState.getEditOperations = vi.fn(() => ({
      datasetId: 'dataset-1',
      episodeIndex: 0,
      subtasks: [
        { id: 's1', label: 'grasp', frameRange: [0, 9], color: '#ff0000', source: 'manual' },
      ],
    }))

    renderWithQuery(
      <ExportDialog open onOpenChange={vi.fn()} datasetId="dataset-1" episodeIndices={[0]} />,
    )
    expect(screen.queryByLabelText(/include subtask metadata/i)).toBeNull()
    await user.click(screen.getByLabelText(/include subtasks as LeRobot subtask annotations/i))
    await user.click(screen.getByRole('button', { name: /start export/i }))

    expect(startExport.mock.calls[0][0].edits[0].subtasks).toBeUndefined()
  })

  it('leaves subtasks out of the request when subtask metadata is unchecked', async () => {
    const user = userEvent.setup()
    const startExport = vi.fn()
    vi.mocked(useExport).mockReturnValue(createUseExportReturn({ startExport }))
    const subtasks = [
      { id: 's1', label: 'grasp', frameRange: [0, 9], color: '#ff0000', source: 'manual' },
    ]
    editState.getEditOperations = vi.fn(() => ({
      datasetId: 'dataset-1',
      episodeIndex: 0,
      removedFrames: [3],
      subtasks,
    }))

    renderWithQuery(
      <ExportDialog open onOpenChange={vi.fn()} datasetId="dataset-1" episodeIndices={[0]} />,
    )
    await user.click(screen.getByLabelText(/include subtask metadata/i))
    await user.click(screen.getByRole('button', { name: /start export/i }))

    const edits = startExport.mock.calls[0][0].edits[0]
    expect(edits.removedFrames).toEqual([3])
    expect(edits.subtasks).toBeUndefined()
  })

  it('renders progress UI and Cancel Export button while exporting', () => {
    vi.mocked(useExport).mockReturnValue(
      createUseExportReturn({
        isExporting: true,
        progress: {
          currentEpisode: 1,
          totalEpisodes: 2,
          currentFrame: 50,
          totalFrames: 100,
          percentage: 50,
          status: 'Exporting frames...',
        },
      }),
    )

    renderWithQuery(
      <ExportDialog open onOpenChange={vi.fn()} datasetId="dataset-1" episodeIndices={[0, 1]} />,
    )

    expect(screen.getByRole('status')).toHaveTextContent('Exporting frames...')
    expect(screen.getByRole('progressbar', { name: 'Export progress' })).toHaveAttribute(
      'aria-valuenow',
      '50',
    )
    expect(screen.getByText(/Episode 1 of 2/i)).toBeInTheDocument()
    expect(screen.getByText(/Frame 50 of 100/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /cancel export/i })).toBeInTheDocument()
  })

  it('calls cancelExport when Cancel Export is clicked during export', async () => {
    const user = userEvent.setup()
    const cancelExport = vi.fn()
    vi.mocked(useExport).mockReturnValue(
      createUseExportReturn({
        isExporting: true,
        progress: {
          currentEpisode: 1,
          totalEpisodes: 2,
          currentFrame: 10,
          totalFrames: 100,
          percentage: 10,
          status: 'Exporting frames...',
        },
        cancelExport,
      }),
    )

    renderWithQuery(
      <ExportDialog open onOpenChange={vi.fn()} datasetId="dataset-1" episodeIndices={[0, 1]} />,
    )

    await user.click(screen.getByRole('button', { name: /cancel export/i }))

    expect(cancelExport).toHaveBeenCalledTimes(1)
  })

  it('shows error state and renders Done button on successful result', async () => {
    vi.mocked(useExport).mockReturnValue(
      createUseExportReturn({
        error: 'Network failure',
        result: { success: false, error: 'Network failure' } as unknown as ReturnType<
          typeof useExport
        >['result'],
      }),
    )

    const { rerender } = renderWithQuery(
      <ExportDialog open onOpenChange={vi.fn()} datasetId="dataset-1" episodeIndices={[0]} />,
    )

    expect(screen.getByText('Export Failed')).toBeInTheDocument()
    expect(screen.getByText('Network failure')).toBeInTheDocument()

    vi.mocked(useExport).mockReturnValue(
      createUseExportReturn({
        result: {
          success: true,
          stats: { totalEpisodes: 1 },
          outputFiles: ['/exports/dataset.hdf5'],
        } as unknown as ReturnType<typeof useExport>['result'],
      }),
    )

    rerender(
      <ExportDialog open onOpenChange={vi.fn()} datasetId="dataset-1" episodeIndices={[0]} />,
    )

    await waitFor(() => {
      expect(screen.getByText('Export Complete')).toBeInTheDocument()
    })
    expect(
      screen.getByText(/Successfully exported 1 episode\(s\) to \/exports\/dataset\.hdf5/i),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^done$/i })).toBeInTheDocument()
  })

  it('calls reset and onOpenChange(false) when Cancel button clicked', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    const reset = vi.fn()
    vi.mocked(useExport).mockReturnValue(createUseExportReturn({ reset }))

    renderWithQuery(
      <ExportDialog open onOpenChange={onOpenChange} datasetId="dataset-1" episodeIndices={[0]} />,
    )

    await user.click(screen.getByRole('button', { name: /^cancel$/i }))

    expect(reset).toHaveBeenCalledTimes(1)
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('displays edited frame count summary when edits remove frames', () => {
    editState.removedFrames = new Set([1, 2, 3])
    vi.mocked(getEffectiveFrameCount).mockReturnValue(97)

    renderWithQuery(
      <ExportDialog open onOpenChange={vi.fn()} datasetId="dataset-1" episodeIndices={[0]} />,
    )

    expect(screen.getByText(/Frames:\s*97/i)).toBeInTheDocument()
    expect(
      screen.getByText(/original:\s*100,\s*removed:\s*3,\s*inserted:\s*0/i),
    ).toBeInTheDocument()
  })

  it('updates outputPath and toggles checkboxes via user input', async () => {
    const user = userEvent.setup()
    const startExport = vi.fn()
    vi.mocked(useExport).mockReturnValue(createUseExportReturn({ startExport }))

    renderWithQuery(
      <ExportDialog open onOpenChange={vi.fn()} datasetId="dataset-1" episodeIndices={[0]} />,
    )

    const input = screen.getByLabelText(/output directory/i)
    await user.clear(input)
    await user.type(input, '/new/path')
    await user.click(screen.getByLabelText(/apply crop/i))
    await user.click(screen.getByLabelText(/include subtask/i))
    await user.click(screen.getByRole('button', { name: /start export/i }))

    expect(startExport).toHaveBeenCalledWith(
      expect.objectContaining({
        outputPath: '/new/path',
        applyEdits: false,
      }),
    )
  })
})
