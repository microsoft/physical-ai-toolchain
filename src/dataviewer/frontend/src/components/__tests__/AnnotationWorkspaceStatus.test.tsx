import './support/annotationWorkspaceTestSupport'

import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AnnotationWorkspace } from '@/components/annotation-workspace/AnnotationWorkspace'

import {
  mockResetEdits,
  mockSaveEpisodeLabels,
  setupAnnotationWorkspaceTestCase,
  teardownAnnotationWorkspaceTestCase,
  testState,
} from './support/annotationWorkspaceTestSupport'

describe('AnnotationWorkspace status and header actions', () => {
  beforeEach(setupAnnotationWorkspaceTestCase)
  afterEach(teardownAnnotationWorkspaceTestCase)

  it('keeps the save status hidden until a save occurs', () => {
    render(<AnnotationWorkspace />)

    expect(screen.queryByText(/changes save automatically/i)).not.toBeInTheDocument()
  })

  it('shows pending episode changes instead of auto-save copy after labels change locally', () => {
    const { rerender } = render(<AnnotationWorkspace />)

    fireEvent.mouseDown(screen.getByRole('tab', { name: /trajectory viewer/i }), { button: 0, ctrlKey: false })
    fireEvent.click(screen.getByRole('button', { name: /toggle label draft/i }))
    rerender(<AnnotationWorkspace />)

    const actions = screen.getByTestId('workspace-header-actions')
    expect(within(actions).getByText(/unsaved episode changes/i)).toBeInTheDocument()
    expect(screen.queryByText(/changes save automatically/i)).not.toBeInTheDocument()
    expect(mockSaveEpisodeLabels).not.toHaveBeenCalled()
  })

  it('shows a saved message after Save & Next Episode and hides it after a short delay', async () => {
    const handleSaveAndNextEpisode = vi.fn()
    const { rerender } = render(<AnnotationWorkspace canGoNextEpisode onSaveAndNextEpisode={handleSaveAndNextEpisode} />)

    testState.episodeLabels = { 0: ['FAILURE'] }
    rerender(<AnnotationWorkspace canGoNextEpisode onSaveAndNextEpisode={handleSaveAndNextEpisode} />)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save\s*&\s*next episode/i }))
      await Promise.resolve()
    })

    expect(screen.getByText(/episode changes saved/i)).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(2500)
    })

    expect(screen.queryByText(/episode changes saved/i)).not.toBeInTheDocument()
  })

  it('does not show stale unsaved episode changes after Save & Next Episode advances to the next episode', async () => {
    const handleSaveAndNextEpisode = vi.fn(() => {
      testState.episodeIndex = 1
      testState.episodeLabels = { ...testState.episodeLabels, 1: [] }
      testState.savedEpisodeLabels = { ...testState.savedEpisodeLabels, 1: [] }
    })
    const { rerender } = render(<AnnotationWorkspace canGoNextEpisode onSaveAndNextEpisode={handleSaveAndNextEpisode} />)

    testState.episodeLabels = { 0: ['FAILURE'], 1: [] }
    testState.savedEpisodeLabels = { 0: ['SUCCESS'], 1: [] }
    rerender(<AnnotationWorkspace canGoNextEpisode onSaveAndNextEpisode={handleSaveAndNextEpisode} />)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save\s*&\s*next episode/i }))
      await Promise.resolve()
    })

    rerender(<AnnotationWorkspace canGoNextEpisode onSaveAndNextEpisode={handleSaveAndNextEpisode} />)
    expect(screen.queryByText(/unsaved episode changes/i)).not.toBeInTheDocument()
  })

  it('uses the save-status slot as the only pending-change indicator', () => {
    testState.hasEdits = true

    render(<AnnotationWorkspace />)

    expect(screen.queryByText(/\(has edits\)/i)).not.toBeInTheDocument()
    expect(screen.getByText(/unsaved episode changes/i)).toBeInTheDocument()
  })

  it('reserves header space so the save status does not shift other controls', () => {
    render(<AnnotationWorkspace />)

    expect(screen.getByTestId('workspace-save-status-slot')).toBeInTheDocument()
  })

  it('allows the workspace header to wrap so actions do not overlap the tab list', () => {
    render(<AnnotationWorkspace />)

    const topBar = screen.getByTestId('workspace-top-bar')
    const headerActions = screen.getByTestId('workspace-header-actions')

    expect(topBar).toContainElement(screen.getByRole('tablist'))
    expect(topBar).toContainElement(headerActions)
    expect(topBar.className).toContain('flex-col')
    expect(headerActions.className).not.toContain('w-full')
    expect(screen.getByRole('heading', { name: /episode 0/i }).compareDocumentPosition(headerActions))
      .toBe(Node.DOCUMENT_POSITION_FOLLOWING)
    expect(headerActions.compareDocumentPosition(screen.getByRole('tablist')))
      .toBe(Node.DOCUMENT_POSITION_FOLLOWING)
  })

  it('adds a dedicated trajectory viewer tab alongside the existing workspace tabs', () => {
    render(<AnnotationWorkspace />)

    expect(screen.queryByRole('tab', { name: /episode viewer/i })).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /trajectory viewer/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /object detection/i })).toBeInTheDocument()
  })

  it('selects the trajectory viewer tab by default', () => {
    render(<AnnotationWorkspace />)

    expect(screen.getByRole('tab', { name: /trajectory viewer/i })).toHaveAttribute('aria-selected', 'true')
  })

  it('renders a Previous Episode action in the workspace header when navigation is available', () => {
    const handlePreviousEpisode = vi.fn()

    render(<AnnotationWorkspace canGoPreviousEpisode onPreviousEpisode={handlePreviousEpisode} />)

    const previousEpisodeButton = screen.getByRole('button', { name: /previous episode/i })

    expect(previousEpisodeButton).toBeEnabled()
    fireEvent.click(previousEpisodeButton)
    expect(handlePreviousEpisode).toHaveBeenCalledTimes(1)
  })

  it('renders a Save & Next Episode action in the workspace header when navigation is available', async () => {
    const handleSaveAndNextEpisode = vi.fn()

    render(<AnnotationWorkspace canGoNextEpisode onSaveAndNextEpisode={handleSaveAndNextEpisode} />)

    const saveAndNextButton = within(screen.getByTestId('workspace-header-actions')).getByRole('button', {
      name: /save\s*&\s*next episode/i,
    })

    expect(saveAndNextButton).toBeEnabled()

    await act(async () => {
      fireEvent.click(saveAndNextButton)
      await Promise.resolve()
    })

    expect(handleSaveAndNextEpisode).toHaveBeenCalledTimes(1)
  })

  it('saves labels and advances when Save & Next Episode is clicked', async () => {
    const handleSaveAndNextEpisode = vi.fn()
    const { rerender } = render(<AnnotationWorkspace canGoNextEpisode onSaveAndNextEpisode={handleSaveAndNextEpisode} />)

    testState.episodeLabels = { 0: ['FAILURE'] }
    rerender(<AnnotationWorkspace canGoNextEpisode onSaveAndNextEpisode={handleSaveAndNextEpisode} />)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save\s*&\s*next episode/i }))
      await Promise.resolve()
    })

    expect(mockSaveEpisodeLabels).toHaveBeenCalledWith({ episodeIdx: 0, labels: ['FAILURE'] })
    expect(handleSaveAndNextEpisode).toHaveBeenCalledTimes(1)
  })

  it('resets labels back to the original episode labels without saving when Reset All is clicked', async () => {
    const { rerender } = render(<AnnotationWorkspace />)

    testState.episodeLabels = { 0: ['FAILURE'] }
    rerender(<AnnotationWorkspace />)

    await act(async () => {
      fireEvent.click(within(screen.getByTestId('workspace-header-actions')).getByRole('button', { name: /^reset all$/i }))
      await Promise.resolve()
    })

    rerender(<AnnotationWorkspace />)

    expect(mockResetEdits).toHaveBeenCalled()
    expect(mockSaveEpisodeLabels).not.toHaveBeenCalled()
    expect(screen.queryByText(/unsaved episode changes/i)).not.toBeInTheDocument()
  })
})
