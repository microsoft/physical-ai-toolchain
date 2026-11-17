import { act, fireEvent, renderHook, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  buildDiagnosticsStateSummary,
  getAvailableDiagnosticsChannels,
  getRecentDiagnosticEvents,
  getVisibleDiagnosticEvents,
  type WorkspaceDiagnosticsSummaryInput,
} from '@/components/annotation-workspace/annotation-workspace-diagnostics'
import { AnnotationWorkspaceDiagnosticsPanel } from '@/components/annotation-workspace/AnnotationWorkspaceDiagnosticsPanel'
import { useAnnotationWorkspaceDiagnostics } from '@/components/annotation-workspace/useAnnotationWorkspaceDiagnostics'
import {
  clearDiagnosticEvents,
  disableDiagnostics,
  enableDiagnostics,
  recordDiagnosticEvent,
} from '@/lib/playback-diagnostics'
import { renderWithQuery } from '@/test-utils/render'

const baseEvents = [
  {
    channel: 'workspace',
    type: 'tab-change',
    data: { nextTab: 'trajectory' },
    timestamp: '2026-03-08T00:00:00.000Z',
  },
  {
    channel: 'playback',
    type: 'sync-action',
    data: { action: 'play' },
    timestamp: '2026-03-08T00:00:01.000Z',
  },
] as const

afterEach(() => {
  disableDiagnostics()
  clearDiagnosticEvents()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('diagnostics interaction state', () => {
  it('copies and downloads only the selected channel and clears visible events', async () => {
    enableDiagnostics()
    clearDiagnosticEvents()
    recordDiagnosticEvent('workspace', 'saved')
    recordDiagnosticEvent('playback', 'paused')
    const writeText = vi.fn().mockResolvedValue(undefined)
    const createObjectURL = vi.fn(() => 'blob:synthetic')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const options = {
      diagnosticsVisible: true,
      activeTab: 'trajectory',
      currentDatasetId: 'synthetic',
      currentEpisodeIndex: 0,
      currentFrame: 0,
      totalFrames: 10,
      isPlaying: false,
      selectedRange: null,
      selectedSubtaskId: null,
    }
    const { result, rerender } = renderHook((props) => useAnnotationWorkspaceDiagnostics(props), {
      initialProps: options,
    })
    act(() => result.current.setSelectedDiagnosticsChannel('playback'))
    await act(async () => result.current.handleCopyDiagnostics())
    expect(JSON.parse(writeText.mock.lastCall![0])).toEqual([
      expect.objectContaining({ channel: 'playback', type: 'paused' }),
    ])
    expect(result.current.diagnosticsClipboardStatus).toBe('Copied diagnostics JSON.')
    act(() => result.current.handleDownloadDiagnostics())
    expect(click).toHaveBeenCalledOnce()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:synthetic')
    act(() => result.current.handleClearVisibleDiagnostics())
    expect(result.current.recentDiagnosticEvents).toEqual([])
    rerender({ ...options, diagnosticsVisible: false })
    expect(result.current.selectedDiagnosticsChannel).toBe('all')
    expect(result.current.diagnosticsClipboardStatus).toBeNull()
  })

  it('announces enabling diagnostics and an unavailable clipboard without writing', async () => {
    disableDiagnostics()
    vi.stubGlobal('navigator', {})
    const options = {
      diagnosticsVisible: false,
      activeTab: 'trajectory',
      currentDatasetId: null,
      currentEpisodeIndex: null,
      currentFrame: 0,
      totalFrames: 0,
      isPlaying: false,
      selectedRange: null,
      selectedSubtaskId: null,
    }
    const { result, rerender } = renderHook((props) => useAnnotationWorkspaceDiagnostics(props), {
      initialProps: options,
    })
    act(() => enableDiagnostics())
    rerender({ ...options, diagnosticsVisible: true })
    expect(
      result.current.recentDiagnosticEvents.some((event) => event.type === 'diagnostics-enabled'),
    ).toBe(true)
    await act(async () => result.current.handleCopyDiagnostics())
    expect(result.current.diagnosticsClipboardStatus).toBe(
      'Clipboard is unavailable in this browser.',
    )
    act(() => result.current.handleDownloadDiagnostics())
  })
})

describe('annotation workspace diagnostics helpers', () => {
  it('returns configured and observed channels while keeping all first', () => {
    expect(getAvailableDiagnosticsChannels(['all', 'workspace'], baseEvents)).toEqual([
      'all',
      'workspace',
      'playback',
    ])
  })

  it('filters visible events by the selected channel', () => {
    expect(getVisibleDiagnosticEvents(baseEvents, 'playback')).toEqual([baseEvents[1]])
    expect(getVisibleDiagnosticEvents(baseEvents, 'all')).toEqual(baseEvents)
  })

  it('keeps only the most recent twelve events and adds unique keys', () => {
    const events = Array.from({ length: 14 }, (_, index) => ({
      channel: 'workspace',
      type: 'event',
      data: { index },
      timestamp: '2026-03-08T00:00:00.000Z',
    }))

    const recentEvents = getRecentDiagnosticEvents(events)

    expect(recentEvents).toHaveLength(12)
    expect(recentEvents[0]?.data).toEqual({ index: 2 })
    expect(new Set(recentEvents.map((event) => event.uniqueKey)).size).toBe(12)
  })

  it('builds a stable workspace summary for the diagnostics panel', () => {
    const input: WorkspaceDiagnosticsSummaryInput = {
      activeTab: 'trajectory',
      currentDatasetId: 'dataset-1',
      currentEpisodeIndex: 7,
      currentFrame: 42,
      totalFrames: 120,
      diagnosticsChannels: ['all', 'workspace', 'playback'],
      isPlaying: true,
      selectedRange: [10, 24],
      selectedSubtaskId: null,
    }

    expect(buildDiagnosticsStateSummary(input)).toEqual([
      { label: 'Dataset', value: 'dataset-1' },
      { label: 'Episode', value: '7' },
      { label: 'Tab', value: 'trajectory' },
      { label: 'Frame', value: '42 / 119' },
      { label: 'Playback', value: 'playing' },
      { label: 'Selection', value: '10-24' },
      { label: 'Channels', value: 'all, workspace, playback' },
    ])
  })
})

describe('AnnotationWorkspaceDiagnosticsPanel', () => {
  it('renders summary rows and recent diagnostic events', () => {
    renderWithQuery(
      <AnnotationWorkspaceDiagnosticsPanel
        diagnosticsStateSummary={[
          { label: 'Dataset', value: 'dataset-1' },
          { label: 'Playback', value: 'paused' },
        ]}
        availableDiagnosticsChannels={['all', 'workspace', 'playback']}
        selectedDiagnosticsChannel="all"
        onSelectedDiagnosticsChannelChange={vi.fn()}
        onClearVisibleDiagnostics={vi.fn()}
        onCopyDiagnostics={vi.fn()}
        onDownloadDiagnostics={vi.fn()}
        diagnosticsClipboardStatus={null}
        recentDiagnosticEvents={getRecentDiagnosticEvents(baseEvents)}
        playbackRangeStart={2}
        playbackRangeEnd={6}
        shouldLoopPlaybackRange={false}
      />,
    )

    expect(screen.getByText(/dataviewer diagnostics/i)).toBeInTheDocument()
    expect(screen.getByText('dataset-1')).toBeInTheDocument()
    expect(screen.getByText(/sync-action/i)).toBeInTheDocument()
    expect(screen.getByText(/loop intent: disabled/i)).toBeInTheDocument()
  })

  it('forwards filter changes and panel actions', () => {
    const handleChannelChange = vi.fn()
    const handleClear = vi.fn()
    const handleCopy = vi.fn()
    const handleDownload = vi.fn()

    renderWithQuery(
      <AnnotationWorkspaceDiagnosticsPanel
        diagnosticsStateSummary={[{ label: 'Dataset', value: 'dataset-1' }]}
        availableDiagnosticsChannels={['all', 'workspace', 'playback']}
        selectedDiagnosticsChannel="all"
        onSelectedDiagnosticsChannelChange={handleChannelChange}
        onClearVisibleDiagnostics={handleClear}
        onCopyDiagnostics={handleCopy}
        onDownloadDiagnostics={handleDownload}
        diagnosticsClipboardStatus="Copied diagnostics JSON."
        recentDiagnosticEvents={[]}
        playbackRangeStart={2}
        playbackRangeEnd={6}
        shouldLoopPlaybackRange
      />,
    )

    fireEvent.change(screen.getByLabelText(/filter events/i), { target: { value: 'playback' } })
    fireEvent.click(screen.getByRole('button', { name: /clear visible events/i }))
    fireEvent.click(screen.getByRole('button', { name: /copy json/i }))
    fireEvent.click(screen.getByRole('button', { name: /download json/i }))

    expect(handleChannelChange).toHaveBeenCalledWith('playback')
    expect(handleClear).toHaveBeenCalledOnce()
    expect(handleCopy).toHaveBeenCalledOnce()
    expect(handleDownload).toHaveBeenCalledOnce()
    expect(screen.getByText(/copied diagnostics json/i)).toBeInTheDocument()
  })
})
