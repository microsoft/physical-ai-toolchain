import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AppContent } from '@/App'
import type { OperatorController } from '@/hooks/use-operator'
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

const { mockOperator, mockStopSession } = vi.hoisted(() => {
  const stopSession = vi.fn()
  const operator: OperatorController = {
    capabilities: {
      enabled: true,
      adapterMode: 'simulated',
      adapterVersion: 1,
      protocolVersion: 1,
      modes: ['teleoperate', 'record'],
      profiles: [],
      reason: null,
    },
    status: {
      serviceInstanceId: 'service-1',
      revision: 0,
      state: 'idle',
      sessionId: '',
      mode: null,
      workerPid: null,
      lastCommand: null,
      cleanupUnconfirmed: false,
      error: null,
    },
    isLoading: false,
    isPending: false,
    error: null,
    connectionState: 'connected',
    telemetry: [],
    preflight: undefined,
    runPreflight: vi.fn(),
    startSession: vi.fn(),
    sendCommand: vi.fn(),
    stopSession,
  }
  return {
    mockStopSession: stopSession,
    mockOperator: operator,
  }
})

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

vi.mock('@/hooks/use-operator', () => ({
  useOperator: () => mockOperator,
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
    localStorage.clear()
    document.documentElement.classList.remove('dark')
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
    mockOperator.status = {
      serviceInstanceId: 'service-1',
      revision: 0,
      state: 'idle',
      sessionId: '',
      mode: null,
      workerPid: null,
      lastCommand: null,
      cleanupUnconfirmed: false,
      error: null,
    }
    mockStopSession.mockClear()
  })

  it('switches away from a removed selected dataset when the dataset list refreshes', async () => {
    const { rerender } = render(<AppContent />)

    await waitFor(() => {
      expect(screen.getByRole('combobox', { name: 'Dataset' })).toHaveTextContent(
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
      expect(screen.getByRole('combobox', { name: 'Dataset' })).toHaveTextContent(
        'customer_lerobot',
      )
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
      expect(screen.getByRole('combobox', { name: 'Dataset' })).toHaveTextContent(
        'customer_lerobot',
      )
    })
  })

  it('uses a compact shell header so the workspace starts higher on the page', async () => {
    render(<AppContent />)

    const banner = await screen.findByRole('banner')

    expect(screen.getByRole('heading', { name: 'Physical AI Training Data' })).toBeInTheDocument()
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

  it('switches between Analyze and Operate without requiring a dataset', async () => {
    const user = userEvent.setup()
    render(<AppContent />)

    expect(screen.getByRole('tab', { name: 'Analyze' })).toHaveAttribute('data-state', 'active')
    expect(screen.getByRole('combobox', { name: 'Dataset' })).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Operate' }))

    expect(screen.getByRole('heading', { name: 'Operator' })).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Dataset' })).not.toBeInTheDocument()
    expect(screen.queryByText('Annotation Workspace')).not.toBeInTheDocument()
  })

  it('switches the shared analyzer and operator theme and persists the choice', async () => {
    const user = userEvent.setup()
    render(<AppContent />)

    const themeButton = screen.getByRole('button', { name: 'Use dark theme' })
    await user.click(themeButton)

    expect(document.documentElement).toHaveClass('dark')
    expect(localStorage.getItem('dataviewer-theme')).toBe('dark')
    expect(screen.getByRole('button', { name: 'Use light theme' })).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Operate' }))
    expect(document.documentElement).toHaveClass('dark')
    expect(screen.getByRole('heading', { name: 'Operator' })).toBeInTheDocument()
  })

  it('keeps Stop Session available in Analyze while a worker is active', async () => {
    mockOperator.status = {
      ...mockOperator.status!,
      state: 'running',
      sessionId: 'session-active',
      mode: 'teleoperate',
      workerPid: 4242,
    }
    const user = userEvent.setup()

    render(<AppContent />)
    await user.click(screen.getByRole('button', { name: 'Stop Session' }))

    expect(mockStopSession).toHaveBeenCalledOnce()
    expect(screen.getByRole('combobox', { name: 'Dataset' })).toBeInTheDocument()
  })
})
