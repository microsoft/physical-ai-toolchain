import './support/annotationWorkspaceTestSupport'

import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AnnotationWorkspace } from '@/components/annotation-workspace/AnnotationWorkspace'

import {
  mockClearDiagnosticEvents,
  mockDiagnosticsState,
  mockRecordDiagnosticEvent,
  setupAnnotationWorkspaceTestCase,
  teardownAnnotationWorkspaceTestCase,
  testState,
} from './support/annotationWorkspaceTestSupport'

describe('AnnotationWorkspace diagnostics', () => {
  beforeEach(setupAnnotationWorkspaceTestCase)
  afterEach(teardownAnnotationWorkspaceTestCase)

  it('keeps the diagnostics panel hidden by default', () => {
    render(<AnnotationWorkspace />)

    expect(screen.queryByTestId('dataviewer-diagnostics-panel')).not.toBeInTheDocument()
  })

  it('shows a whole-dataviewer diagnostics panel when diagnostics are enabled from the shell', () => {
    mockDiagnosticsState.enabled = true
    mockDiagnosticsState.channels = ['all']

    render(<AnnotationWorkspace diagnosticsVisible />)

    expect(screen.getByTestId('dataviewer-diagnostics-panel')).toBeInTheDocument()
  })

  it('keeps the workspace header actions free of the diagnostics toggle', () => {
    render(<AnnotationWorkspace />)

    expect(
      within(screen.getByTestId('workspace-header-actions')).queryByRole('button', {
        name: /toggle diagnostics/i,
      }),
    ).not.toBeInTheDocument()
  })

  it('renders diagnostics in a shared bottom panel outside the trajectory tab', () => {
    mockDiagnosticsState.enabled = true
    mockDiagnosticsState.channels = ['all', 'workspace', 'playback']
    mockDiagnosticsState.events = [
      {
        channel: 'workspace',
        type: 'tab-change',
        data: { nextTab: 'episode' },
        timestamp: '2026-03-08T00:00:00.000Z',
      },
      {
        channel: 'playback',
        type: 'sync-action',
        data: { action: 'play' },
        timestamp: '2026-03-08T00:00:01.000Z',
      },
    ]

    render(<AnnotationWorkspace />)

    const diagnosticsPanel = screen.getByTestId('dataviewer-diagnostics-panel')
    expect(diagnosticsPanel).toBeInTheDocument()
    expect(screen.getByText(/dataviewer diagnostics/i)).toBeInTheDocument()
    expect(screen.getByText(/workspace state/i)).toBeInTheDocument()
    expect(within(diagnosticsPanel).getByText(/sync-action/i)).toBeInTheDocument()
    expect(screen.queryByTestId('playback-diagnostics-panel')).not.toBeInTheDocument()
  })

  it('updates subgroup loop intent when the auto-loop toggle changes', () => {
    mockDiagnosticsState.enabled = true
    mockDiagnosticsState.channels = ['all']
    testState.autoLoop = true

    const { rerender } = render(<AnnotationWorkspace diagnosticsVisible />)

    fireEvent.click(screen.getByRole('button', { name: /select saved subtask/i }))
    expect(screen.getByText(/loop intent: enabled/i)).toBeInTheDocument()

    testState.autoLoop = false
    rerender(<AnnotationWorkspace diagnosticsVisible />)
    expect(screen.getByText(/loop intent: disabled/i)).toBeInTheDocument()
  })

  it('filters diagnostics events by channel and clears only the visible channel history', () => {
    mockDiagnosticsState.enabled = true
    mockDiagnosticsState.channels = ['all', 'labels', 'playback']
    mockDiagnosticsState.events = [
      {
        channel: 'labels',
        type: 'draft-change',
        data: { labels: ['FAILURE'] },
        timestamp: '2026-03-08T00:00:00.000Z',
      },
      {
        channel: 'playback',
        type: 'sync-action',
        data: { action: 'play' },
        timestamp: '2026-03-08T00:00:01.000Z',
      },
    ]

    render(<AnnotationWorkspace />)

    const diagnosticsPanel = screen.getByTestId('dataviewer-diagnostics-panel')
    fireEvent.change(within(diagnosticsPanel).getByLabelText(/filter events/i), {
      target: { value: 'labels' },
    })

    expect(within(diagnosticsPanel).getByText(/draft-change/i)).toBeInTheDocument()
    expect(within(diagnosticsPanel).queryByText(/sync-action/i)).not.toBeInTheDocument()

    fireEvent.click(within(diagnosticsPanel).getByRole('button', { name: /clear visible events/i }))

    expect(mockClearDiagnosticEvents).toHaveBeenCalledWith('labels')
    expect(within(diagnosticsPanel).queryByText(/draft-change/i)).not.toBeInTheDocument()
    expect(
      within(diagnosticsPanel).getByText(/no diagnostics events recorded yet/i),
    ).toBeInTheDocument()
  })

  it('copies the visible diagnostics events as json from the shared panel', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })

    mockDiagnosticsState.enabled = true
    mockDiagnosticsState.channels = ['all', 'export']
    mockDiagnosticsState.events = [
      {
        channel: 'export',
        type: 'dialog-open',
        data: { activeTab: 'trajectory' },
        timestamp: '2026-03-08T00:00:00.000Z',
      },
    ]

    render(<AnnotationWorkspace />)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /copy json/i }))
      await Promise.resolve()
    })

    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('"channel": "export"'))
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('"type": "dialog-open"'))
  })

  it('records expanded diagnostics channels for labels, subtasks, export, detection, and persistence actions', async () => {
    const handleSaveAndNextEpisode = vi.fn()
    mockDiagnosticsState.enabled = true
    mockDiagnosticsState.channels = ['all']
    testState.hasEdits = true

    const { rerender } = render(
      <AnnotationWorkspace canGoNextEpisode onSaveAndNextEpisode={handleSaveAndNextEpisode} />,
    )

    mockRecordDiagnosticEvent.mockClear()

    fireEvent.click(screen.getByRole('button', { name: /export/i }))
    fireEvent.mouseDown(screen.getByRole('tab', { name: /trajectory viewer/i }), {
      button: 0,
      ctrlKey: false,
    })
    fireEvent.click(screen.getByRole('button', { name: /toggle label draft/i }))
    rerender(
      <AnnotationWorkspace canGoNextEpisode onSaveAndNextEpisode={handleSaveAndNextEpisode} />,
    )
    fireEvent.click(screen.getByRole('button', { name: /create subtask/i }))
    fireEvent.mouseDown(screen.getByRole('tab', { name: /object detection/i }), {
      button: 0,
      ctrlKey: false,
    })

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save\s*&\s*next episode/i }))
      await Promise.resolve()
    })

    expect(mockRecordDiagnosticEvent.mock.calls).toEqual(
      expect.arrayContaining([
        ['labels', 'draft-change', expect.objectContaining({ episodeIndex: 0, labelCount: 1 })],
        ['export', 'dialog-open', expect.objectContaining({ activeTab: 'trajectory' })],
        ['subtasks', 'create', expect.objectContaining({ rangeStart: 2, rangeEnd: 6 })],
        ['detection', 'tab-viewed', expect.objectContaining({ previousTab: 'trajectory' })],
        ['persistence', 'draft-saved', expect.objectContaining({ episodeIndex: 0 })],
      ]),
    )
  })
})
