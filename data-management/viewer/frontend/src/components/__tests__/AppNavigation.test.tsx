import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AppContent } from '@/App'
import { datasetKeys } from '@/hooks/use-datasets'
import type { OperatorController } from '@/hooks/use-operator'

let operator: OperatorController

vi.mock('@/hooks/use-datasets', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/use-datasets')>()
  return {
    ...actual,
    useDatasets: () => ({ data: [] }),
    useCapabilities: () => ({ data: undefined }),
    useEpisodes: () => ({ data: [] }),
  }
})

vi.mock('@/hooks/use-dataviewer-shell-state', () => ({
  useDataviewerShellState: () => ({
    datasetId: '',
    diagnosticsVisible: false,
    selectedEpisode: 0,
    setDatasetId: vi.fn(),
    setSelectedEpisode: vi.fn(),
    toggleDiagnostics: vi.fn(),
    isWarmingCache: false,
  }),
}))

vi.mock('@/hooks/use-joint-config', () => ({ useJointConfig: vi.fn() }))
vi.mock('@/hooks/use-labels', () => ({ useDatasetLabels: vi.fn() }))
vi.mock('@/hooks/use-operator', () => ({ useOperator: () => operator }))

vi.mock('@/components/app-shell/DataviewerShellHeader', () => ({
  DataviewerShellHeader: () => <header>Analysis header</header>,
}))
vi.mock('@/components/app-shell/DataviewerEpisodeList', () => ({
  DataviewerEpisodeList: () => <aside>Episode list</aside>,
}))
vi.mock('@/components/app-shell/DataviewerEpisodeViewer', () => ({
  DataviewerEpisodeViewer: () => <main>Analysis workspace</main>,
}))
vi.mock('@/components/operator', () => ({
  OperatorWorkspace: () => <main>Operator workspace</main>,
}))

function createOperator(enabled: boolean): OperatorController {
  return {
    capabilities: {
      enabled,
      adapterMode: enabled ? 'simulated' : 'disabled',
      adapterVersion: 1,
      protocolVersion: 2,
      modes: enabled ? ['teleoperate', 'record'] : [],
      profiles: [],
      robots: [],
      cameras: [],
      preflightEnabled: false,
      sessionStartEnabled: enabled,
      reason: enabled ? null : 'Operator disabled',
    },
    status: {
      serviceInstanceId: 'service-1',
      revision: 1,
      state: 'idle',
      sessionId: '',
      mode: null,
      workerPid: null,
      lastCommand: null,
      cleanupUnconfirmed: false,
      error: null,
      targetHz: null,
      actualHz: null,
      loopP95Ms: null,
      loopMaxMs: null,
      overruns: 0,
      latestWorkerLog: null,
      latestTelemetry: null,
      sessionSettings: null,
      datasetId: null,
      episodeIndex: 0,
      recordingPhase: null,
      uploadStatus: 'not_requested',
      uploadError: null,
    },
    calibration: undefined,
    preflight: undefined,
    telemetry: [],
    connectionState: enabled ? 'connected' : 'disabled',
    isLoading: false,
    isPending: false,
    error: null,
    calibrationError: null,
    checkCalibration: vi.fn(),
    runPreflight: vi.fn(),
    startSession: vi.fn(),
    sendCommand: vi.fn(),
    stopSession: vi.fn(),
  }
}

function renderApp(queryClient = new QueryClient()) {
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <AppContent />
      </QueryClientProvider>,
    ),
  }
}

describe('App operator navigation and recording handoff', () => {
  beforeEach(() => {
    operator = createOperator(true)
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('shows operator navigation only when the capability is enabled', async () => {
    const user = userEvent.setup()
    const { rerender, queryClient } = renderApp()

    await user.click(screen.getByRole('tab', { name: 'Live operator' }))
    expect(screen.getByText('Operator workspace')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Analysis' }))
    expect(screen.getByText('Analysis workspace')).toBeInTheDocument()

    operator = createOperator(false)
    rerender(
      <QueryClientProvider client={queryClient}>
        <AppContent />
      </QueryClientProvider>,
    )

    expect(screen.queryByRole('tab', { name: 'Live operator' })).not.toBeInTheDocument()
    expect(screen.getByText('Analysis workspace')).toBeInTheDocument()
  })

  it('invalidates dataset discovery once when a recording completes', async () => {
    const { queryClient, rerender } = renderApp()
    const invalidateQueries = vi.spyOn(queryClient, 'invalidateQueries')

    operator = {
      ...operator,
      status: {
        ...operator.status!,
        revision: 2,
        state: 'completed',
        sessionId: 'recording-1',
        mode: 'record',
        datasetId: 'recorded-dataset',
      },
    }
    rerender(
      <QueryClientProvider client={queryClient}>
        <AppContent />
      </QueryClientProvider>,
    )

    await waitFor(() => {
      expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: datasetKeys.list() })
    })
    expect(invalidateQueries).toHaveBeenCalledOnce()

    rerender(
      <QueryClientProvider client={queryClient}>
        <AppContent />
      </QueryClientProvider>,
    )
    expect(invalidateQueries).toHaveBeenCalledOnce()
  })
})
