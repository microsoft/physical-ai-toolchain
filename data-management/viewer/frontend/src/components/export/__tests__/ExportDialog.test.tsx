import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ExportDialog } from '@/components/export/ExportDialog'
import { useExport } from '@/hooks/use-export'
import { getEffectiveFrameCount, useEditStore, useEpisodeStore } from '@/stores'
import { renderWithQuery } from '@/test-utils/render'

vi.mock('@/hooks/use-export', () => ({
  useExport: vi.fn(),
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
        includeSubtasks: true,
        format: 'hdf5',
      }),
    )
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

    expect(screen.getByText('Exporting frames...')).toBeInTheDocument()
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
      screen.getByText(/Successfully exported 1 episode\(s\) to 1 file\(s\)\./i),
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
        includeSubtasks: false,
      }),
    )
  })
})
