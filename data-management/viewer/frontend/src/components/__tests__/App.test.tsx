import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AppContent } from '@/App'
import { useDatasetStore, useEpisodeStore } from '@/stores'
import { useLabelStore } from '@/stores/label-store'
import type { DatasetInfo } from '@/types'

const { mockIsDiagnosticsEnabled, mockEnableDiagnostics, mockDisableDiagnostics } = vi.hoisted(
  () => ({
    mockIsDiagnosticsEnabled: vi.fn(() => false),
    mockEnableDiagnostics: vi.fn(),
    mockDisableDiagnostics: vi.fn(),
  }),
)

let mockDatasets: DatasetInfo[] = []
let mockCatalogError: Error | null = null
const mockCatalogDiagnostic = vi.fn()

vi.mock('@/hooks/use-datasets', () => ({
  useDatasets: () => ({ data: mockDatasets }),
  useDataset: (id?: string) => ({ data: mockDatasets.find((dataset) => dataset.id === id) }),
  useDatasetCatalog: (options: { query?: string; offset?: number; limit?: number }) => ({
    data: {
      items: mockDatasets
        .filter((dataset) =>
          `${dataset.id} ${dataset.name}`.toLowerCase().includes(options.query ?? ''),
        )
        .slice(options.offset ?? 0, (options.offset ?? 0) + (options.limit ?? 25)),
      total: mockDatasets.length,
      catalogTotal: mockDatasets.length,
      groups: [],
      snapshotId: 'catalog-revision',
      offset: 0,
      limit: 25,
      stale: false,
      refreshFailed: false,
    },
    isLoading: false,
    isFetching: false,
    error: mockCatalogError,
    refreshCatalog: vi.fn(),
  }),
  useCapabilities: () => ({ data: undefined }),
  useEpisodes: () => ({
    data: [
      { index: 0, length: 12, taskIndex: 0, hasAnnotations: false },
      { index: 1, length: 10, taskIndex: 0, hasAnnotations: false },
      { index: 2, length: 8, taskIndex: 0, hasAnnotations: false },
    ],
    isLoading: false,
    error: null,
  }),
  useEpisode: (_datasetId: string, episodeIndex: number) => ({
    data: {
      sourceId: 'synthetic-source',
      sourceRevision: 'synthetic-revision',
      meta: { index: episodeIndex, length: 12 },
      videoUrls: undefined,
      cameras: [],
      trajectoryData: undefined,
    },
    isLoading: false,
    error: null,
  }),
}))

vi.mock('@/hooks/use-joint-config', () => ({
  useJointConfig: () => undefined,
}))

vi.mock('@/hooks/use-vlm-judge-batch', () => ({
  useJudgeDataset: () => ({
    inventory: {},
    jobs: {},
    approvals: {},
    reset: {},
    review: {},
    act: vi.fn(),
    refresh: vi.fn(),
  }),
  useVlmJudgeBatch: () => ({ submit: vi.fn(), isPending: false }),
  useJudgeSamples: () => ({ data: [] }),
}))

vi.mock('@/hooks/use-episode-readiness', () => ({
  useEpisodeReadiness: () => ({ ready: true }),
}))

vi.mock('@/hooks/use-principal-context', () => ({
  usePrincipalContext: () => ({ data: { scopeId: 'principal-one' } }),
}))

vi.mock('@/hooks/use-labels', () => ({
  useDatasetLabels: () => undefined,
}))

vi.mock('@/lib/playback-diagnostics', () => ({
  recordDiagnosticEvent: (...args: unknown[]) => mockCatalogDiagnostic(...args),
  disableDiagnostics: mockDisableDiagnostics,
  enableDiagnostics: mockEnableDiagnostics,
  isDiagnosticsEnabled: mockIsDiagnosticsEnabled,
}))

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  warmCache: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('@/components/annotation-panel', () => ({
  LabelFilter: () => <div>Label Filter</div>,
}))

vi.mock('@/components/annotation-workspace/AnnotationWorkspace', () => ({
  AnnotationWorkspace: ({
    canGoPreviousEpisode,
    onPreviousEpisode,
    canGoNextEpisode,
    onNextEpisode,
    onSaveAndNextEpisode,
  }: {
    canGoPreviousEpisode?: boolean
    onPreviousEpisode?: () => void
    canGoNextEpisode?: boolean
    onNextEpisode?: () => void
    onSaveAndNextEpisode?: () => void
  }) => (
    <div>
      <div>Annotation Workspace</div>
      <button type="button" disabled={!canGoPreviousEpisode} onClick={onPreviousEpisode}>
        Previous Episode
      </button>
      <button type="button" disabled={!canGoNextEpisode} onClick={onNextEpisode}>
        Next Episode
      </button>
      <button type="button" disabled={!canGoNextEpisode} onClick={onSaveAndNextEpisode}>
        Save and Next Episode
      </button>
    </div>
  ),
}))

describe('AppContent', () => {
  it('replaces both episode panes while retaining drafts and frame state without autoplay', async () => {
    const user = userEvent.setup()
    render(<AppContent />)
    const editor = await screen.findByText('Annotation Workspace')
    act(() => {
      useEpisodeStore.setState({ currentFrame: 7, isPlaying: true })
      useLabelStore.getState().setEpisodeLabels(0, ['human draft'])
    })
    await user.click(screen.getByRole('button', { name: 'Dataset workspace' }))
    expect(editor).not.toBeVisible()
    expect(screen.getByText('Label Filter')).not.toBeVisible()
    expect(screen.getByRole('button', { name: 'Dataset workspace' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
    expect(useEpisodeStore.getState().isPlaying).toBe(false)
    await user.click(screen.getByRole('button', { name: 'Return to episode' }))
    expect(editor).toBeVisible()
    expect(useEpisodeStore.getState().currentFrame).toBe(7)
    expect(useEpisodeStore.getState().isPlaying).toBe(false)
    expect(useLabelStore.getState().episodeLabels[0]).toEqual(['human draft'])
    expect(screen.getByRole('button', { name: 'Dataset workspace' })).toHaveFocus()
  })
  it('retains catalog summaries on refresh failure without logging private error content', async () => {
    window.history.replaceState(null, '', '/')
    mockCatalogError = new Error('private storage query')
    render(<AppContent />)
    expect(screen.getByRole('alert')).toHaveTextContent('Catalog unavailable')
    expect(screen.getByRole('option', { name: /houston_lerobot_fixed/ })).toBeInTheDocument()
    await waitFor(() =>
      expect(mockCatalogDiagnostic).toHaveBeenCalledWith('navigation', 'catalog-fetch-error', {
        retained: true,
      }),
    )
    expect(JSON.stringify(mockCatalogDiagnostic.mock.calls)).not.toContain('private')
  })
  it('pages a large catalog and restores the search and row focus after selection', async () => {
    window.history.replaceState(null, '', '/')
    mockDatasets = Array.from({ length: 1000 }, (_, index) => ({
      id: `group--${String(index).padStart(4, '0')}`,
      name: 'Repeated name',
      totalEpisodes: index,
      fps: 30,
      features: {},
      tasks: [],
    }))
    const user = userEvent.setup()
    render(<AppContent />)
    expect(screen.getAllByRole('option')).toHaveLength(29)
    await user.click(screen.getByRole('button', { name: 'Next catalog page' }))
    expect(screen.getByRole('option', { name: /group--0025/ })).toBeInTheDocument()
    await user.type(screen.getByRole('combobox', { name: 'Filter datasets' }), '0999')
    await user.keyboard('{ArrowDown}{Enter}')
    expect(await screen.findByText('Annotation Workspace')).toBeVisible()
    act(() => useEpisodeStore.setState({ isPlaying: true }))
    await user.click(screen.getByRole('button', { name: 'Dataset' }))
    expect(useEpisodeStore.getState().isPlaying).toBe(false)
    expect(screen.getByRole('combobox', { name: 'Filter datasets' })).toHaveValue('0999')
    expect(screen.getByRole('option', { name: /group--0999/ })).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(useEpisodeStore.getState().isPlaying).toBe(false)
    expect(screen.getByRole('button', { name: 'Dataset' })).toHaveFocus()
  })
  it('starts in the catalog without selecting or mounting an episode workspace', async () => {
    window.history.replaceState(null, '', '/')
    render(<AppContent />)
    expect(await screen.findByRole('heading', { name: 'Datasets' })).toBeInTheDocument()
    expect(screen.queryByText('Annotation Workspace')).not.toBeInTheDocument()
    expect(useDatasetStore.getState().currentDataset).toBeNull()
  })
  beforeEach(() => {
    mockCatalogError = null
    mockCatalogDiagnostic.mockClear()
    window.history.replaceState(null, '', '/?dataset=houston_lerobot_fixed')
    mockDatasets = [
      {
        id: 'houston_lerobot_fixed',
        name: 'houston_lerobot_fixed (ur10e)',
        totalEpisodes: 100,
        fps: 30,
        features: {},
        tasks: [],
      },
      {
        id: 'customer_lerobot',
        name: 'customer_lerobot (hexagarm)',
        totalEpisodes: 64,
        fps: 30,
        features: {},
        tasks: [],
      },
    ]
    useDatasetStore.getState().reset()
    useEpisodeStore.getState().reset()
    useLabelStore.getState().reset()
  })

  afterEach(() => {
    cleanup()
    window.history.replaceState(null, '', '/')
  })

  beforeEach(() => {
    mockIsDiagnosticsEnabled.mockReturnValue(false)
    mockEnableDiagnostics.mockClear()
    mockDisableDiagnostics.mockClear()
  })

  it('does not silently replace a directly selected dataset when catalog contents change', async () => {
    const { rerender } = render(<AppContent />)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Dataset' })).toHaveTextContent(
        'houston_lerobot_fixed',
      )
    })

    mockDatasets = [
      {
        id: 'customer_lerobot',
        name: 'customer_lerobot (hexagarm)',
        totalEpisodes: 64,
        fps: 30,
        features: {},
        tasks: [],
      },
    ]

    rerender(<AppContent />)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Dataset' })).toHaveTextContent(
        'houston_lerobot_fixed',
      )
    })
  })

  it('renders a filterable dataset dropdown even when only one dataset is available', async () => {
    window.history.replaceState(null, '', '/?dataset=customer_lerobot')
    mockDatasets = [
      {
        id: 'customer_lerobot',
        name: 'customer_lerobot',
        totalEpisodes: 64,
        fps: 30,
        features: {},
        tasks: [],
      },
    ]

    const user = userEvent.setup()

    render(<AppContent />)

    const trigger = await screen.findByRole('button', { name: 'Dataset' })
    expect(trigger).toHaveTextContent('customer_lerobot')
    expect(screen.queryByPlaceholderText('Dataset ID')).not.toBeInTheDocument()

    await user.click(trigger)

    expect(screen.getByPlaceholderText('Filter datasets')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /customer_lerobot/ })).toBeInTheDocument()
  })

  it('supports keyboard selection from the dataset dropdown results', async () => {
    const user = userEvent.setup()

    render(<AppContent />)

    const trigger = await screen.findByRole('button', { name: 'Dataset' })
    expect(trigger).toHaveTextContent('houston_lerobot_fixed')

    await user.click(trigger)
    await user.type(screen.getByPlaceholderText('Filter datasets'), 'hex')
    await user.keyboard('{ArrowDown}{Enter}')

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Dataset' })).toHaveTextContent('customer_lerobot')
    })
  })

  it('uses a compact shell header so the workspace starts higher on the page', async () => {
    render(<AppContent />)

    const banner = await screen.findByRole('banner')

    expect(banner.className).toContain('py-2.5')
    expect(banner.className).toContain('px-4')
    expect(banner.className).not.toContain('py-4')
    expect(banner.className).not.toContain('px-6')
  })

  it('renders a compact diagnostics button next to the dataset picker in the shell header', async () => {
    render(<AppContent />)

    const banner = await screen.findByRole('banner')
    const diagnosticsButton = screen.getByRole('button', { name: /toggle diagnostics/i })
    const datasetPicker = screen.getByRole('button', { name: 'Dataset' })

    expect(banner).toContainElement(diagnosticsButton)
    expect(diagnosticsButton.className).toContain('h-8')
    expect(diagnosticsButton.className).toContain('px-3')
    expect(datasetPicker.parentElement).toContainElement(diagnosticsButton)
    expect(diagnosticsButton).toHaveAttribute('aria-pressed', 'false')
  })

  it('advances to the next episode from the workspace top bar action', async () => {
    const user = userEvent.setup()

    render(<AppContent />)

    await screen.findByText('Annotation Workspace')

    await user.click(screen.getByRole('button', { name: /^next episode$/i }))

    await waitFor(() => {
      expect(useEpisodeStore.getState().currentEpisode?.meta.index).toBe(1)
    })
  })

  it('moves back to the previous episode from the workspace top bar action', async () => {
    const user = userEvent.setup()

    render(<AppContent />)

    await screen.findByText('Annotation Workspace')

    await user.click(screen.getByRole('button', { name: /^next episode$/i }))
    await user.click(screen.getByRole('button', { name: /previous episode/i }))

    await waitFor(() => {
      expect(useEpisodeStore.getState().currentEpisode?.meta.index).toBe(0)
    })
  })

  it('advances from the workspace save-and-next action', async () => {
    const user = userEvent.setup()

    render(<AppContent />)

    await screen.findByText('Annotation Workspace')

    await user.click(screen.getByRole('button', { name: /save and next episode/i }))

    await waitFor(() => {
      expect(useEpisodeStore.getState().currentEpisode?.meta.index).toBe(1)
    })
  })

  it('uses a single compact sidebar toolbar for filters and episode count', async () => {
    render(<AppContent />)

    const sidebarToolbar = await screen.findByTestId('episode-list-toolbar')

    expect(sidebarToolbar).toHaveTextContent('Label Filter')
    expect(sidebarToolbar).toHaveTextContent('3 Episodes')
    expect(sidebarToolbar.className).toContain('border-b')
    expect(sidebarToolbar.className).toContain('py-1.5')
  })
  it('gives the focused filter input combobox ownership and restores trigger focus', async () => {
    const user = userEvent.setup()
    render(<AppContent />)

    const trigger = await screen.findByRole('button', { name: 'Dataset' })
    await user.click(trigger)

    const filter = screen.getByRole('combobox', { name: 'Filter datasets' })
    expect(filter).toHaveAttribute('aria-expanded', 'true')
    const listboxId = filter.getAttribute('aria-controls')
    expect(listboxId).toBeTruthy()
    expect(document.getElementById(listboxId!)).toHaveAttribute('role', 'listbox')
    expect(filter).toHaveFocus()

    await user.keyboard('{Escape}')

    expect(trigger).toHaveFocus()
    expect(screen.queryByRole('combobox', { name: 'Filter datasets' })).not.toBeInTheDocument()
  })
  it('stacks the shell and sidebar at narrow widths', async () => {
    render(<AppContent />)

    const main = await screen.findByRole('main')
    const layout = main.parentElement
    const sidebar = layout?.querySelector('aside')

    expect(layout).toHaveClass('flex-col', 'sm:flex-row')
    expect(sidebar).toHaveClass('w-full', 'sm:w-64')
    expect(main).toHaveClass('min-w-0')
  })
  it('exposes help and problem-reporting actions without dataset content', async () => {
    render(<AppContent />)

    expect(await screen.findByRole('link', { name: 'Help' })).toHaveAttribute(
      'href',
      'https://github.com/microsoft/physical-ai-toolchain/tree/main/data-management/viewer',
    )
    expect(screen.getByRole('link', { name: 'Report problem' })).toHaveAttribute(
      'href',
      'https://github.com/microsoft/physical-ai-toolchain/issues/new?template=01-bug-report.yml',
    )
  })
})
