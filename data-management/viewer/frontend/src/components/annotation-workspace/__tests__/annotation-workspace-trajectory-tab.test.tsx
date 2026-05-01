import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { AnnotationWorkspaceTrajectoryTab } from '@/components/annotation-workspace/AnnotationWorkspaceTrajectoryTab'
import { Tabs } from '@/components/ui/tabs'

vi.mock('@/components/episode-viewer', () => ({
  TrajectoryPlot: () => <div data-testid="trajectory-plot" />,
}))

vi.mock('@/components/subtask-timeline', () => ({
  SubtaskToolbar: ({
    onSelectionChange,
  }: {
    selectedSegmentId: string | null
    onSelectionChange: (id: string | null) => void
  }) => (
    <button data-testid="subtask-toolbar" onClick={() => onSelectionChange('toolbar-id')}>
      toolbar
    </button>
  ),
  SubtaskTimelineTrack: ({
    onSegmentClick,
  }: {
    onSegmentClick: (segment: { id: string }) => void
  }) => (
    <button data-testid="subtask-segment" onClick={() => onSegmentClick({ id: 'segment-42' })}>
      segment
    </button>
  ),
}))

function renderTab(overrides: Record<string, unknown> = {}) {
  const defaults = {
    playbackCard: <div data-testid="playback-card" />,
    subtaskListCard: <div data-testid="subtask-list-card" />,
    labelPanel: <div data-testid="label-panel" />,
    languageInstructionPanel: <div data-testid="language-panel" />,
    editToolsPanel: <div data-testid="edit-tools-panel" />,
    selectedRange: null,
    selectedSubtaskId: null,
    onClearPlaybackSelection: vi.fn(),
    onDraftRangeChange: vi.fn(),
    onCreateSubtaskFromRange: vi.fn(),
    onGraphSeek: vi.fn(),
    onSelectionStart: vi.fn(),
    onSelectionComplete: vi.fn(),
    totalFrames: 100,
    onSubtaskSelectionChange: vi.fn(),
  }
  const props = { ...defaults, ...overrides }
  return {
    props,
    ...render(
      <Tabs defaultValue="trajectory">
        <AnnotationWorkspaceTrajectoryTab {...props} />
      </Tabs>,
    ),
  }
}

describe('AnnotationWorkspaceTrajectoryTab', () => {
  it('renders the slot content for playback, label, language, and edit panels', () => {
    renderTab()
    expect(screen.getByTestId('playback-card')).toBeInTheDocument()
    expect(screen.getByTestId('label-panel')).toBeInTheDocument()
    expect(screen.getByTestId('language-panel')).toBeInTheDocument()
    expect(screen.getByTestId('edit-tools-panel')).toBeInTheDocument()
  })

  it('hides Clear Selection when no range or subtask is selected', () => {
    renderTab()
    expect(screen.queryByRole('button', { name: 'Clear Selection' })).not.toBeInTheDocument()
  })

  it('shows Clear Selection when a range is selected and forwards the click', () => {
    const { props } = renderTab({ selectedRange: [10, 20] })
    fireEvent.click(screen.getByRole('button', { name: 'Clear Selection' }))
    expect(props.onClearPlaybackSelection).toHaveBeenCalled()
  })

  it('shows Clear Selection when a subtask is selected', () => {
    renderTab({ selectedSubtaskId: 'subtask-1' })
    expect(screen.getByRole('button', { name: 'Clear Selection' })).toBeInTheDocument()
  })

  it('forwards segment clicks from SubtaskTimelineTrack via onSubtaskSelectionChange', () => {
    const { props } = renderTab()
    fireEvent.click(screen.getByTestId('subtask-segment'))
    expect(props.onSubtaskSelectionChange).toHaveBeenCalledWith('segment-42')
  })

  it('forwards toolbar selection changes', () => {
    const { props } = renderTab()
    fireEvent.click(screen.getByTestId('subtask-toolbar'))
    expect(props.onSubtaskSelectionChange).toHaveBeenCalledWith('toolbar-id')
  })
})
