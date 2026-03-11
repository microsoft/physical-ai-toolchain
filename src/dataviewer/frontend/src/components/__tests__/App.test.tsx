import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AppContent } from '@/App'
import { useDatasetStore, useEpisodeStore } from '@/stores'
import { useLabelStore } from '@/stores/label-store'
import type { DatasetInfo } from '@/types'

const { mockIsDiagnosticsEnabled, mockEnableDiagnostics, mockDisableDiagnostics } = vi.hoisted(() => ({
  mockIsDiagnosticsEnabled: vi.fn(() => false),
  mockEnableDiagnostics: vi.fn(),
  mockDisableDiagnostics: vi.fn(),
}))

let mockDatasets: DatasetInfo[] = []

vi.mock('@/hooks/use-datasets', () => ({
  useDatasets: () => ({ data: mockDatasets }),
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

vi.mock('@/hooks/use-labels', () => ({
  useDatasetLabels: () => undefined,
}))

vi.mock('@/lib/playback-diagnostics', () => ({
  disableDiagnostics: mockDisableDiagnostics,
  enableDiagnostics: mockEnableDiagnostics,
  isDiagnosticsEnabled: mockIsDiagnosticsEnabled,
}))

vi.mock('@/lib/api-client', () => ({
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
  beforeEach(() => {
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

  afterEach(cleanup)

  beforeEach(() => {
    mockIsDiagnosticsEnabled.mockReturnValue(false)
    mockEnableDiagnostics.mockClear()
    mockDisableDiagnostics.mockClear()
  })

  it('switches away from a removed selected dataset when the dataset list refreshes', async () => {
    const { rerender } = render(<AppContent />)

    await waitFor(() => {
      expect(screen.getByRole('combobox', { name: 'Dataset' })).toHaveTextContent('houston_lerobot_fixed')
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
      expect(screen.getByRole('combobox', { name: 'Dataset' })).toHaveTextContent('customer_lerobot')
    })
  })

  it('renders a filterable dataset dropdown even when only one dataset is available', async () => {
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

    const trigger = await screen.findByRole('combobox', { name: 'Dataset' })
    expect(trigger).toHaveTextContent('customer_lerobot')
    expect(screen.queryByPlaceholderText('Dataset ID')).not.toBeInTheDocument()

    await user.click(trigger)

    expect(screen.getByPlaceholderText('Filter datasets')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'customer_lerobot' })).toBeInTheDocument()
  })

  it('supports keyboard selection from the dataset dropdown results', async () => {
    const user = userEvent.setup()

    render(<AppContent />)

    const trigger = await screen.findByRole('combobox', { name: 'Dataset' })
    expect(trigger).toHaveTextContent('houston_lerobot_fixed')

    await user.click(trigger)
    await user.type(screen.getByPlaceholderText('Filter datasets'), 'hex')
    await user.keyboard('{ArrowDown}{Enter}')

    await waitFor(() => {
      expect(screen.getByRole('combobox', { name: 'Dataset' })).toHaveTextContent('customer_lerobot')
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
    const datasetPicker = screen.getByRole('combobox', { name: 'Dataset' })

    expect(banner).toContainElement(diagnosticsButton)
    expect(diagnosticsButton.className).toContain('h-8')
    expect(diagnosticsButton.className).toContain('px-3')
    expect(datasetPicker.parentElement).toContainElement(diagnosticsButton)
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
})
