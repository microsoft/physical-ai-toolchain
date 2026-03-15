import './support/annotationWorkspaceTestSupport'

import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { AnnotationWorkspace } from '@/components/annotation-workspace/AnnotationWorkspace'

import {
  mediaSpies,
  mockComputeSyncAction,
  mockSetCurrentFrame,
  mockTogglePlayback,
  setupAnnotationWorkspaceTestCase,
  teardownAnnotationWorkspaceTestCase,
  testState,
} from './support/annotationWorkspaceTestSupport'

describe('AnnotationWorkspace playback and trajectory tab flows', () => {
  beforeEach(setupAnnotationWorkspaceTestCase)
  afterEach(teardownAnnotationWorkspaceTestCase)

  it('defaults the workspace to the trajectory viewer without an episode tab', () => {
    render(<AnnotationWorkspace />)

    expect(screen.queryByRole('tab', { name: /episode viewer/i })).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /trajectory viewer/i })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(screen.getByTestId('trajectory-graph-panel')).toBeInTheDocument()
  })

  it('shows subtask controls in the default trajectory viewer', () => {
    render(<AnnotationWorkspace />)

    expect(screen.getByText('Subtask Toolbar')).toBeInTheDocument()
    expect(screen.getByText('Subtask Timeline Track')).toBeInTheDocument()
  })

  it('renders the shared subtask list in the default trajectory viewer', () => {
    render(<AnnotationWorkspace />)

    expect(screen.getByText('Subtask List')).toBeInTheDocument()
  })

  it('keeps the default trajectory viewer playback group focused on playback and subtasks without edit tools', () => {
    render(<AnnotationWorkspace />)

    const playbackGroup = screen.getByTestId('trajectory-playback-group-panel')

    expect(within(playbackGroup).queryByText('Edit Tools')).not.toBeInTheDocument()
  })

  it('renders the trajectory plot after switching to the trajectory viewer tab', () => {
    render(<AnnotationWorkspace />)

    fireEvent.mouseDown(screen.getByRole('tab', { name: /trajectory viewer/i }), {
      button: 0,
      ctrlKey: false,
    })
    expect(screen.getByText('Trajectory Plot')).toBeInTheDocument()
  })

  it('renders subtask controls alongside the trajectory graph in the trajectory viewer tab', () => {
    render(<AnnotationWorkspace />)

    fireEvent.mouseDown(screen.getByRole('tab', { name: /trajectory viewer/i }), {
      button: 0,
      ctrlKey: false,
    })
    expect(screen.getByText('Subtask Toolbar')).toBeInTheDocument()
    expect(screen.getByText('Subtask Timeline Track')).toBeInTheDocument()
  })

  it('renders the same subtask list in the trajectory viewer under the compact video panel', () => {
    render(<AnnotationWorkspace />)

    fireEvent.mouseDown(screen.getByRole('tab', { name: /trajectory viewer/i }), {
      button: 0,
      ctrlKey: false,
    })
    expect(screen.getByText('Subtask List')).toBeInTheDocument()
  })

  it('moves episode labels into the trajectory playback grouping instead of the default edit tools column', () => {
    render(<AnnotationWorkspace />)

    expect(screen.getByTestId('trajectory-labels-panel')).toContainElement(
      screen.getByText('Toggle Label Draft'),
    )
  })

  it('renders episode labels in their own trajectory panel beside the stacked playback and graph panels on large layouts', () => {
    render(<AnnotationWorkspace />)

    fireEvent.mouseDown(screen.getByRole('tab', { name: /trajectory viewer/i }), {
      button: 0,
      ctrlKey: false,
    })

    const trajectoryLayout = screen.getByTestId('trajectory-layout-grid')
    const labelsPanel = screen.getByTestId('trajectory-labels-panel')

    expect(trajectoryLayout.className).toContain('lg:grid-cols-3')
    expect(labelsPanel.className).toContain('lg:row-span-2')
    expect(labelsPanel).not.toContainElement(screen.getByTestId('trajectory-compact-media-frame'))
    expect(labelsPanel).not.toContainElement(screen.getByText('Subtask List'))
  })

  it('renders the remaining edit tools under the labels inside the trajectory side panel', () => {
    render(<AnnotationWorkspace />)

    fireEvent.mouseDown(screen.getByRole('tab', { name: /trajectory viewer/i }), {
      button: 0,
      ctrlKey: false,
    })

    const labelsPanel = screen.getByTestId('trajectory-labels-panel')

    expect(labelsPanel).toContainElement(screen.getByText('Episode Labels'))
    expect(labelsPanel).toContainElement(screen.getByText('Edit Tools'))
    expect(labelsPanel).toContainElement(screen.getByText('Frame Removal'))
    expect(labelsPanel).toContainElement(screen.getByText('Trajectory Adjustment'))
  })

  it('constrains the compact trajectory playback media frame so it does not dominate the viewer', () => {
    render(<AnnotationWorkspace />)

    fireEvent.mouseDown(screen.getByRole('tab', { name: /trajectory viewer/i }), {
      button: 0,
      ctrlKey: false,
    })

    expect(screen.getByTestId('trajectory-compact-media-frame').className).toContain(
      'max-w-[40rem]',
    )
  })

  it('groups the trajectory playback and subtask list into their own scrollable panel', () => {
    render(<AnnotationWorkspace />)

    fireEvent.mouseDown(screen.getByRole('tab', { name: /trajectory viewer/i }), {
      button: 0,
      ctrlKey: false,
    })

    const playbackGroupPanel = screen.getByTestId('trajectory-playback-group-panel')

    expect(playbackGroupPanel.className).toContain('overflow-y-auto')
    expect(playbackGroupPanel).toContainElement(screen.getByText('Subtask List'))
  })

  it('keeps the trajectory graph grouping below the playback and subtask grouping', () => {
    render(<AnnotationWorkspace />)

    fireEvent.mouseDown(screen.getByRole('tab', { name: /trajectory viewer/i }), {
      button: 0,
      ctrlKey: false,
    })

    expect(
      screen
        .getByTestId('trajectory-playback-group-panel')
        .compareDocumentPosition(screen.getByTestId('trajectory-graph-panel')),
    ).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
  })

  it('keeps the outer trajectory tab panel free of its own vertical scrollbar', () => {
    render(<AnnotationWorkspace />)

    fireEvent.mouseDown(screen.getByRole('tab', { name: /trajectory viewer/i }), {
      button: 0,
      ctrlKey: false,
    })

    expect(screen.getByRole('tabpanel', { name: /trajectory viewer/i }).className).not.toContain(
      'overflow-y-auto',
    )
  })

  it('renders the trajectory graph inside its own scrollable panel', () => {
    render(<AnnotationWorkspace />)

    fireEvent.mouseDown(screen.getByRole('tab', { name: /trajectory viewer/i }), {
      button: 0,
      ctrlKey: false,
    })

    const graphPanel = screen.getByTestId('trajectory-graph-panel')

    expect(graphPanel.className).toContain('overflow-y-auto')
    expect(graphPanel).toContainElement(screen.getByText('Trajectory Plot'))
  })

  it('clears a draft graph selection when Escape is pressed', () => {
    render(<AnnotationWorkspace />)

    fireEvent.mouseDown(screen.getByRole('tab', { name: /trajectory viewer/i }), {
      button: 0,
      ctrlKey: false,
    })
    fireEvent.click(screen.getByRole('button', { name: /select range draft/i }))

    expect(screen.getByRole('button', { name: /clear selection/i })).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('button', { name: /clear selection/i })).not.toBeInTheDocument()
  })

  it('pauses during graph range selection and resumes from the selection start when playback was already running', () => {
    testState.isPlaying = true

    render(<AnnotationWorkspace />)

    fireEvent.mouseDown(screen.getByRole('tab', { name: /trajectory viewer/i }), {
      button: 0,
      ctrlKey: false,
    })
    fireEvent.click(screen.getByRole('button', { name: /start range drag/i }))
    fireEvent.click(screen.getAllByRole('button', { name: /finish range drag/i })[1])

    expect(mockTogglePlayback).toHaveBeenCalledTimes(2)
    expect(mockSetCurrentFrame).toHaveBeenLastCalledWith(2)
  })

  it('keeps a graph range selection paused when playback was already paused', () => {
    testState.isPlaying = false

    render(<AnnotationWorkspace />)

    fireEvent.mouseDown(screen.getByRole('tab', { name: /trajectory viewer/i }), {
      button: 0,
      ctrlKey: false,
    })
    fireEvent.click(screen.getByRole('button', { name: /start range drag/i }))
    fireEvent.click(screen.getAllByRole('button', { name: /finish range drag/i })[1])

    expect(mockTogglePlayback).not.toHaveBeenCalled()
    expect(mockSetCurrentFrame).toHaveBeenLastCalledWith(2)
  })

  it('restarts playback when a remounted video finishes loading while the store is already playing', () => {
    testState.isPlaying = true
    mockComputeSyncAction.mockReturnValue({ kind: 'play', playbackRate: 1 })

    const { container } = render(<AnnotationWorkspace />)
    const video = container.querySelector('video')

    expect(video).not.toBeNull()
    Object.defineProperty(video!, 'duration', { configurable: true, value: 12.8 })
    mediaSpies.play?.mockClear()

    fireEvent.loadedMetadata(video!)
    expect(mediaSpies.play).toHaveBeenCalledTimes(1)
  })

  it('marks the trajectory playback controls as keep-selection controls for draft ranges', () => {
    render(<AnnotationWorkspace />)

    fireEvent.mouseDown(screen.getByRole('tab', { name: /trajectory viewer/i }), {
      button: 0,
      ctrlKey: false,
    })
    fireEvent.click(screen.getByRole('button', { name: /select range draft/i }))

    expect(
      screen
        .getByRole('button', { name: /play playback/i })
        .closest('[data-keep-playback-selection="true"]'),
    ).not.toBeNull()
  })

  it('uses compact playback controls in the trajectory viewer tab', () => {
    render(<AnnotationWorkspace />)

    fireEvent.mouseDown(screen.getByRole('tab', { name: /trajectory viewer/i }), {
      button: 0,
      ctrlKey: false,
    })

    expect(screen.getByRole('button', { name: /play playback/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /toggle auto-play/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /toggle loop playback/i })).toBeInTheDocument()
    expect(screen.queryByText(/^Speed:$/)).not.toBeInTheDocument()
  })
})
