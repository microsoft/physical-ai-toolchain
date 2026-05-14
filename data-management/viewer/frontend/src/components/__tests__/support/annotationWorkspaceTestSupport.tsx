import { cleanup } from '@testing-library/react'
import { vi } from 'vitest'

const hoisted = vi.hoisted(() => {
  const diagnosticsState = {
    enabled: false,
    channels: [] as string[],
    events: [] as Array<{
      channel: string
      type: string
      data?: Record<string, unknown>
      timestamp: string
    }>,
  }

  const state = {
    episodeLabels: { 0: ['SUCCESS'] } as Record<number, string[]>,
    savedEpisodeLabels: { 0: ['SUCCESS'] } as Record<number, string[]>,
    availableLabels: ['SUCCESS', 'FAILURE', 'PARTIAL'],
    labelsLoaded: true,
    episodeIndex: 0,
    hasEdits: false,
    isPlaying: false,
    autoPlay: false,
    autoLoop: false,
    subtasks: [{ id: 'subtask-1', frameRange: [2, 6] as [number, number] }],
  }

  const clearDiagnosticEvents = vi.fn((channel?: string) => {
    if (!channel) {
      diagnosticsState.events = []
      return
    }

    diagnosticsState.events = diagnosticsState.events.filter((event) => event.channel !== channel)
  })

  const disableDiagnostics = vi.fn(() => {
    diagnosticsState.enabled = false
    diagnosticsState.channels = []
  })

  const enableDiagnostics = vi.fn((channels?: string[] | string) => {
    diagnosticsState.enabled = true
    diagnosticsState.channels = !channels
      ? ['all']
      : Array.isArray(channels)
        ? channels
        : [channels]
  })

  const recordDiagnosticEvent = vi.fn(
    (channel: string, type: string, data?: Record<string, unknown>) => {
      if (!diagnosticsState.enabled) {
        return
      }

      diagnosticsState.events.push({ channel, type, data, timestamp: new Date().toISOString() })
    },
  )

  return {
    diagnosticsState,
    state,
    clearDiagnosticEvents,
    disableDiagnostics,
    enableDiagnostics,
    recordDiagnosticEvent,
    computeSyncAction: vi.fn<() => { kind: string; playbackRate?: number }>(() => ({
      kind: 'pause',
    })),
    initializeEdit: vi.fn(),
    resetEdits: vi.fn(),
    saveEpisodeDraft: vi.fn(),
    setCurrentFrame: vi.fn(),
    togglePlayback: vi.fn(),
    setPlaybackSpeed: vi.fn(),
    setAutoPlay: vi.fn(),
    setAutoLoop: vi.fn(),
    saveEpisodeLabels: vi.fn(),
  }
})

export const mockComputeSyncAction = hoisted.computeSyncAction
export const mockDiagnosticsState = hoisted.diagnosticsState
export const mockClearDiagnosticEvents = hoisted.clearDiagnosticEvents
export const mockDisableDiagnostics = hoisted.disableDiagnostics
export const mockEnableDiagnostics = hoisted.enableDiagnostics
export const mockRecordDiagnosticEvent = hoisted.recordDiagnosticEvent
export const testState = hoisted.state
export const mockInitializeEdit = hoisted.initializeEdit
export const mockResetEdits = hoisted.resetEdits
export const mockSaveEpisodeDraft = hoisted.saveEpisodeDraft
export const mockSetCurrentFrame = hoisted.setCurrentFrame
export const mockTogglePlayback = hoisted.togglePlayback
export const mockSetPlaybackSpeed = hoisted.setPlaybackSpeed
export const mockSetAutoPlay = hoisted.setAutoPlay
export const mockSetAutoLoop = hoisted.setAutoLoop
export const mockSaveEpisodeLabels = hoisted.saveEpisodeLabels

vi.mock('@/components/annotation-panel', () => ({
  LabelPanel: () => (
    <div>
      <h3>Episode Labels</h3>
      <button
        type="button"
        onClick={() => {
          hoisted.state.episodeLabels = { 0: ['FAILURE'] }
        }}
      >
        Toggle Label Draft
      </button>
    </div>
  ),
  LanguageInstructionWidget: () => <div>Language Instructions</div>,
}))

vi.mock('@/components/episode-viewer', () => ({
  CameraSelector: (props: Record<string, unknown>) => {
    const cameraProps = props as {
      cameras?: string[]
      selectedCamera?: string
      onSelectCamera?: (camera: string) => void
    }
    return (
      <select
        aria-label="Camera"
        value={cameraProps.selectedCamera ?? ''}
        onChange={(event) => cameraProps.onSelectCamera?.(event.target.value)}
      >
        {(cameraProps.cameras ?? []).map((camera) => (
          <option key={camera} value={camera}>
            {camera}
          </option>
        ))}
      </select>
    )
  },
  TrajectoryPlot: (props: Record<string, unknown>) => {
    const plotProps = props as {
      selectedRange?: [number, number] | null
      onSelectedRangeChange?: (range: [number, number] | null) => void
      onCreateSubtaskFromRange?: (range: [number, number]) => void
      onSelectionStart?: () => void
      onSelectionComplete?: (range: [number, number]) => void
    }

    return (
      <div>
        <div>Trajectory Plot</div>
        <div>
          {plotProps.selectedRange
            ? `Selected range ${plotProps.selectedRange[0]}-${plotProps.selectedRange[1]}`
            : 'No selected range'}
        </div>
        <button type="button" onClick={() => plotProps.onSelectedRangeChange?.([2, 6])}>
          Select Range Draft
        </button>
        <button type="button" onClick={() => plotProps.onSelectionStart?.()}>
          Start Range Drag
        </button>
        <button type="button" onClick={() => plotProps.onSelectionComplete?.([2, 6])}>
          Finish Range Drag
        </button>
        <button
          type="button"
          onClick={() => {
            plotProps.onSelectedRangeChange?.([2, 6])
            plotProps.onSelectionComplete?.([2, 6])
          }}
        >
          Finish Range Drag
        </button>
        <button type="button" onClick={() => plotProps.onCreateSubtaskFromRange?.([2, 6])}>
          Create Subtask
        </button>
      </div>
    )
  },
}))

vi.mock('@/components/export', () => ({ ExportDialog: () => null }))

vi.mock('@/components/frame-editor', () => ({
  ColorAdjustmentControls: () => <div>Color Adjustment Controls</div>,
  FrameInsertionToolbar: () => <div>Frame Insertion Toolbar</div>,
  FrameRemovalToolbar: () => <div>Frame Removal</div>,
  TrajectoryEditor: () => <div>Trajectory Editor</div>,
  TransformControls: () => <div>Transform Controls</div>,
}))

vi.mock('@/components/object-detection', () => ({
  DetectionPanel: () => <div>Detection Panel</div>,
}))

vi.mock('@/components/playback/PlaybackControlStrip', () => ({
  PlaybackControlStrip: ({ controls }: { controls?: React.JSX.Element | null }) => (
    <div>
      <div>Playback Control Strip</div>
      {controls}
    </div>
  ),
}))

vi.mock('@/components/subtask-timeline', () => ({
  SubtaskList: ({ onSelectionChange }: { onSelectionChange?: (id: string | null) => void }) => (
    <div>
      <div>Subtask List</div>
      <button type="button" onClick={() => onSelectionChange?.('subtask-1')}>
        Select Saved Subtask
      </button>
    </div>
  ),
  SubtaskTimelineTrack: () => <div>Subtask Timeline Track</div>,
  SubtaskToolbar: () => <div>Subtask Toolbar</div>,
}))

vi.mock('@/components/viewer-display', () => ({
  ViewerDisplayControls: () => <div>Viewer Display Controls</div>,
}))
vi.mock('@/lib/css-filters', () => ({ combineCssFilters: () => '' }))

vi.mock('@/lib/playback-diagnostics', () => ({
  DIAGNOSTIC_CHANNEL_OPTIONS: [
    'all',
    'workspace',
    'playback',
    'labels',
    'subtasks',
    'persistence',
    'export',
    'navigation',
    'detection',
  ],
  DIAGNOSTICS_EVENT_NAME: 'dataviewer:diagnostics',
  clearDiagnosticEvents: hoisted.clearDiagnosticEvents,
  disableDiagnostics: hoisted.disableDiagnostics,
  enableDiagnostics: hoisted.enableDiagnostics,
  getEnabledDiagnosticsChannels: () =>
    hoisted.diagnosticsState.enabled ? hoisted.diagnosticsState.channels : [],
  isDiagnosticsEnabled: () => hoisted.diagnosticsState.enabled,
  isDiagnosticsChannelEnabled: () => hoisted.diagnosticsState.enabled,
  readDiagnosticEvents: (channel?: string) => {
    if (!channel) {
      return hoisted.diagnosticsState.events
    }

    return hoisted.diagnosticsState.events.filter((event) => event.channel === channel)
  },
  recordDiagnosticEvent: hoisted.recordDiagnosticEvent,
  stringifyDiagnosticEvents: (events: unknown[]) => JSON.stringify(events, null, 2),
}))

vi.mock('@/lib/playback-utils', () => ({
  clampFrameToPlaybackRange: (frame: number) => frame,
  computeEffectiveFps: () => 30,
  computeSyncAction: hoisted.computeSyncAction,
  resolvePlaybackRange: (totalFrames: number, range: [number, number] | null) =>
    range ?? [0, totalFrames - 1],
  resolvePlaybackTick: (frame: number) => ({ frame, shouldStop: false }),
  shouldLoopActivePlaybackRange: (range: [number, number] | null, autoLoop: boolean) =>
    (autoLoop && !!range) || (range === null && autoLoop),
  shouldRecoverPlaybackAfterDesync: () => false,
  shouldRestartPlaybackAfterLoop: () => false,
}))

vi.mock('@/hooks/use-labels', () => ({
  useSaveEpisodeLabels: () => ({
    mutateAsync: hoisted.saveEpisodeLabels,
    isPending: false,
  }),
}))

vi.mock('@/hooks/use-datasets', () => ({
  useCacheStats: () => ({ data: undefined }),
}))

vi.mock('@/stores/label-store', () => ({
  useLabelStore: (selector: (state: unknown) => unknown) =>
    selector({
      isLoaded: hoisted.state.labelsLoaded,
      availableLabels: hoisted.state.availableLabels,
      episodeLabels: hoisted.state.episodeLabels,
      savedEpisodeLabels: hoisted.state.savedEpisodeLabels,
      setEpisodeLabels: (episodeIndex: number, labels: string[]) => {
        hoisted.state.episodeLabels = { ...hoisted.state.episodeLabels, [episodeIndex]: labels }
      },
      commitEpisodeLabels: (episodeIndex: number, labels?: string[]) => {
        const nextLabels = labels ?? hoisted.state.episodeLabels[episodeIndex] ?? []

        hoisted.state.episodeLabels = { ...hoisted.state.episodeLabels, [episodeIndex]: nextLabels }
        hoisted.state.savedEpisodeLabels = {
          ...hoisted.state.savedEpisodeLabels,
          [episodeIndex]: nextLabels,
        }
      },
    }),
}))

vi.mock('@/stores', () => ({
  useDatasetStore: (selector: (state: unknown) => unknown) =>
    selector({ currentDataset: { id: 'dataset-1', fps: 30 } }),
  useEditDirtyState: () => ({ isDirty: hoisted.state.hasEdits, resetEdits: hoisted.resetEdits }),
  useFrameInsertionState: () => ({
    insertedFrames: new Map<number, { interpolationFactor?: number }>(),
  }),
  useEditStore: (selector: (state: unknown) => unknown) =>
    selector({
      subtasks: hoisted.state.subtasks,
      addSubtask: vi.fn(),
      removedFrames: new Set<number>(),
      initializeEdit: hoisted.initializeEdit,
      clearTransforms: vi.fn(),
      saveEpisodeDraft: hoisted.saveEpisodeDraft,
      datasetId: null,
      episodeIndex: null,
      globalTransform: null,
    }),
  useEpisodeStore: (selector: (state: unknown) => unknown) =>
    selector({
      currentEpisode: {
        meta: { index: hoisted.state.episodeIndex, length: 12 },
        videoUrls: { main: '/video.mp4' },
        cameras: ['main'],
        trajectoryData: undefined,
      },
    }),
  usePlaybackControls: () => ({
    currentFrame: 0,
    isPlaying: hoisted.state.isPlaying,
    playbackSpeed: 1,
    setCurrentFrame: hoisted.setCurrentFrame,
    togglePlayback: hoisted.togglePlayback,
    setPlaybackSpeed: hoisted.setPlaybackSpeed,
  }),
  usePlaybackSettings: () => ({
    autoPlay: hoisted.state.autoPlay,
    autoLoop: hoisted.state.autoLoop,
    setAutoPlay: hoisted.setAutoPlay,
    setAutoLoop: hoisted.setAutoLoop,
  }),
  useViewerDisplay: () => ({ displayAdjustment: null, isActive: false }),
}))

vi.mock('@/stores/edit-store', () => ({
  getEffectiveFrameCount: () => 12,
  getOriginalIndex: () => 0,
}))

export const mediaSpies: {
  play: ReturnType<typeof vi.spyOn> | null
  pause: ReturnType<typeof vi.spyOn> | null
} = {
  play: null,
  pause: null,
}

export function setupAnnotationWorkspaceTestCase() {
  vi.useFakeTimers()
  mediaSpies.play = vi
    .spyOn(HTMLMediaElement.prototype, 'play')
    .mockImplementation(() => Promise.resolve())
  mediaSpies.pause = vi
    .spyOn(HTMLMediaElement.prototype, 'pause')
    .mockImplementation(() => undefined)

  testState.episodeLabels = { 0: ['SUCCESS'] }
  testState.savedEpisodeLabels = { 0: ['SUCCESS'] }
  testState.availableLabels = ['SUCCESS', 'FAILURE', 'PARTIAL']
  testState.labelsLoaded = true
  testState.episodeIndex = 0
  testState.hasEdits = false
  testState.isPlaying = false
  testState.autoPlay = false
  testState.autoLoop = false
  testState.subtasks = [{ id: 'subtask-1', frameRange: [2, 6] }]

  mockSaveEpisodeLabels.mockReset()
  mockSetCurrentFrame.mockReset()
  mockTogglePlayback.mockReset()
  mockSetPlaybackSpeed.mockReset()
  mockSetAutoPlay.mockReset()
  mockSetAutoLoop.mockReset()
  mockComputeSyncAction.mockReset()
  mockComputeSyncAction.mockReturnValue({ kind: 'pause' })
  mockSaveEpisodeLabels.mockImplementation(
    async ({ episodeIdx, labels }: { episodeIdx: number; labels: string[] }) => {
      testState.episodeLabels = { ...testState.episodeLabels, [episodeIdx]: labels }
      testState.savedEpisodeLabels = { ...testState.savedEpisodeLabels, [episodeIdx]: labels }
      return undefined
    },
  )
  mockSaveEpisodeDraft.mockReset()
  mockSaveEpisodeDraft.mockImplementation(() => {
    testState.hasEdits = false
  })
  mockResetEdits.mockReset()
  mockDiagnosticsState.enabled = false
  mockDiagnosticsState.channels = []
  mockDiagnosticsState.events = []
  mockClearDiagnosticEvents.mockClear()
  mockEnableDiagnostics.mockClear()
  mockDisableDiagnostics.mockClear()
  mockRecordDiagnosticEvent.mockClear()
}

export function teardownAnnotationWorkspaceTestCase() {
  cleanup()
  mediaSpies.play?.mockRestore()
  mediaSpies.pause?.mockRestore()
  mediaSpies.play = null
  mediaSpies.pause = null
  vi.runOnlyPendingTimers()
  vi.useRealTimers()
}
