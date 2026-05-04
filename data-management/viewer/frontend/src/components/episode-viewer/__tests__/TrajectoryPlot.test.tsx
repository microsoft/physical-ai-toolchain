import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { TrajectoryPlot } from '../TrajectoryPlot'
import { useTrajectoryPlotState } from '../useTrajectoryPlotState'

vi.mock('../useTrajectoryPlotState', () => ({
  useTrajectoryPlotState: vi.fn(),
}))

vi.mock('../TrajectoryPlotControls', () => ({
  TrajectoryPlotControls: () => <div data-testid="trajectory-controls" />,
}))

vi.mock('../TrajectoryPlotChart', () => ({
  TrajectoryPlotChart: () => <div data-testid="trajectory-chart" />,
}))

interface DefaultsEditorProps {
  open: boolean
  isSaving?: boolean
  onSave: (config: { groups: unknown; labels: unknown }) => void
}

const defaultsEditorMock = vi.fn()
vi.mock('../JointConfigDefaultsEditor', () => ({
  JointConfigDefaultsEditor: (props: DefaultsEditorProps) => {
    defaultsEditorMock(props)
    return (
      <div
        data-testid="defaults-editor"
        data-open={String(props.open)}
        data-saving={String(props.isSaving ?? false)}
      />
    )
  },
}))

const mockedState = vi.mocked(useTrajectoryPlotState)

interface StateOverrides {
  currentEpisode?: unknown
  chartData?: unknown[]
  saveDefaultsMutate?: ReturnType<typeof vi.fn>
  setDefaultsOpen?: ReturnType<typeof vi.fn>
}

function buildState(overrides: StateOverrides = {}) {
  return {
    currentEpisode: 'currentEpisode' in overrides ? overrides.currentEpisode : { id: 'ep-1' },
    currentFrame: 0,
    setCurrentFrame: vi.fn(),
    trajectoryAdjustments: {},
    jointConfig: { groups: [], labels: {} },
    updateLabel: vi.fn(),
    updateGroupLabel: vi.fn(),
    createGroup: vi.fn(),
    deleteGroup: vi.fn(),
    moveJoint: vi.fn(),
    defaults: null,
    saveDefaults: { mutate: overrides.saveDefaultsMutate ?? vi.fn(), isPending: false },
    selectedJoints: [],
    setSelectedJoints: vi.fn(),
    showVelocity: false,
    setShowVelocity: vi.fn(),
    showNormalized: false,
    setShowNormalized: vi.fn(),
    defaultsOpen: false,
    setDefaultsOpen: overrides.setDefaultsOpen ?? vi.fn(),
    plotArea: null,
    selectionSurfaceRef: { current: null },
    withSave: (fn: unknown) => fn,
    resolveLabel: (idx: number) => `joint-${idx}`,
    chartData: overrides.chartData ?? [{ frame: 0 }],
    jointCount: 1,
    selection: {
      handleSelectionContextMenu: vi.fn(),
      handleSelectionPointerDown: vi.fn(),
      handleSelectionPointerMove: vi.fn(),
      handleSelectionPointerUp: vi.fn(),
      dismissContextMenu: vi.fn(),
      contextMenuPosition: null,
    },
    selectionHighlight: null,
    handleChartClick: vi.fn(),
    isNormalizationDisabled: false,
    toggleNormalization: vi.fn(),
  }
}

describe('TrajectoryPlot', () => {
  afterEach(() => {
    vi.resetAllMocks()
  })

  it('renders the placeholder when no episode is loaded', () => {
    mockedState.mockReturnValue(buildState({ currentEpisode: null }) as never)
    render(<TrajectoryPlot />)
    expect(screen.getByText('No episode selected')).toBeInTheDocument()
  })

  it('renders the empty-data placeholder when chartData is empty', () => {
    mockedState.mockReturnValue(buildState({ chartData: [] }) as never)
    render(<TrajectoryPlot />)
    expect(screen.getByText('No trajectory data available')).toBeInTheDocument()
  })

  it('renders controls, chart, and defaults editor when data is present', () => {
    mockedState.mockReturnValue(buildState() as never)
    render(<TrajectoryPlot />)
    expect(screen.getByTestId('trajectory-controls')).toBeInTheDocument()
    expect(screen.getByTestId('trajectory-chart')).toBeInTheDocument()
    expect(screen.getByTestId('defaults-editor')).toBeInTheDocument()
  })

  it('saves defaults to the _defaults dataset and closes the editor on success', () => {
    const setDefaultsOpen = vi.fn()
    const onSaved = vi.fn()
    const mutate = vi.fn((_payload: unknown, options?: { onSuccess?: () => void }) => {
      options?.onSuccess?.()
    })
    mockedState.mockReturnValue(
      buildState({ saveDefaultsMutate: mutate, setDefaultsOpen }) as never,
    )

    render(<TrajectoryPlot onSaved={onSaved} />)
    const props = defaultsEditorMock.mock.calls[0][0] as DefaultsEditorProps
    const config = { groups: [{ id: 'g', label: 'G', indices: [0] }], labels: { '0': 'a' } }
    props.onSave(config)

    expect(mutate).toHaveBeenCalledWith(
      { datasetId: '_defaults', ...config },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    )
    expect(setDefaultsOpen).toHaveBeenCalledWith(false)
    expect(onSaved).toHaveBeenCalledTimes(1)
  })
})
