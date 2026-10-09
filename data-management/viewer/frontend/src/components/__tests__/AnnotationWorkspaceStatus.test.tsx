import './support/annotationWorkspaceTestSupport'

import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AnnotationWorkspace } from '@/components/annotation-workspace/AnnotationWorkspace'

import {
  mockResetEdits,
  mockSaveAnnotation,
  mockSaveEpisodeLabels,
  setupAnnotationWorkspaceTestCase,
  teardownAnnotationWorkspaceTestCase,
  testState,
} from './support/annotationWorkspaceTestSupport'

describe('AnnotationWorkspace status and header actions', () => {
  beforeEach(setupAnnotationWorkspaceTestCase)
  afterEach(teardownAnnotationWorkspaceTestCase)

  it('loads annotation state from the workspace even when the language widget is unmounted', () => {
    render(<AnnotationWorkspace />)
    expect(testState.annotationReadCount).toBeGreaterThan(0)
  })

  it('blocks whole-episode Save and announces annotation recovery failures', () => {
    testState.annotationRecoveryError = 'Annotation draft recovery failed.'
    render(<AnnotationWorkspace />)
    expect(screen.getByRole('button', { name: /^save episode$/i })).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('Annotation draft recovery failed.')
    expect(mockSaveAnnotation).not.toHaveBeenCalled()
  })

  it.each([false, true])(
    'blocks whole-episode Save when label recovery is incomplete: %s',
    (failed) => {
      testState.labelDraftHydrated = false
      testState.labelRecoveryError = failed ? 'Label draft recovery failed.' : null
      render(<AnnotationWorkspace />)
      expect(screen.getByRole('button', { name: /^save episode$/i })).toBeDisabled()
      expect(screen.getByRole('status')).toHaveTextContent(
        failed ? 'Label draft recovery failed.' : 'Loading label drafts.',
      )
      expect(mockSaveEpisodeLabels).not.toHaveBeenCalled()
    },
  )

  it('includes annotation-only changes in standalone Save', async () => {
    testState.hasAnnotationChanges = true
    render(<AnnotationWorkspace />)
    expect(screen.getByRole('status')).toHaveTextContent(/unsaved episode changes/i)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^save episode$/i }))
    })
    expect(mockSaveAnnotation).toHaveBeenCalledWith(
      expect.objectContaining({
        datasetId: 'dataset-1',
        episodeIndex: 0,
        annotation: expect.objectContaining({ annotatorId: 'principal-test' }),
      }),
    )
  })

  it('saves the whole episode through Ctrl+S', async () => {
    testState.hasAnnotationChanges = true
    testState.episodeLabels = { 0: ['FAILURE'] }
    render(<AnnotationWorkspace />)
    await act(async () => {
      fireEvent.keyDown(window, { key: 's', ctrlKey: true })
    })
    expect(mockSaveAnnotation).toHaveBeenCalledOnce()
    expect(mockSaveEpisodeLabels).toHaveBeenCalledOnce()
  })

  it('retains partial failures and retries only the remaining resource', async () => {
    testState.hasAnnotationChanges = true
    testState.episodeLabels = { 0: ['FAILURE'] }
    mockSaveAnnotation.mockRejectedValueOnce(new Error('Forbidden'))
    const { rerender } = render(<AnnotationWorkspace />)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^save episode$/i }))
    })
    expect(screen.getByRole('status')).toHaveTextContent(/some episode changes could not be saved/i)
    expect(testState.savedEpisodeLabels[0]).toEqual(['FAILURE'])
    mockSaveAnnotation.mockImplementationOnce(async () => {
      testState.hasAnnotationChanges = false
    })
    rerender(<AnnotationWorkspace />)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^save episode$/i }))
    })
    expect(mockSaveEpisodeLabels).toHaveBeenCalledOnce()
    expect(mockSaveAnnotation).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('status')).toHaveTextContent(/episode changes saved/i)
  })

  it('keeps edits made during Save dirty and excludes duplicate submissions', async () => {
    testState.episodeLabels = { 0: ['FAILURE'] }
    let finish!: () => void
    const pending = new Promise<void>((resolve) => {
      finish = resolve
    })
    mockSaveEpisodeLabels.mockImplementationOnce(async ({ labels }: { labels: string[] }) => {
      await pending
      testState.savedEpisodeLabels = { 0: labels }
    })
    const { rerender } = render(<AnnotationWorkspace />)
    const saveButton = screen.getByRole('button', { name: /^save episode$/i })
    saveButton.focus()
    await act(async () => {
      fireEvent.click(saveButton)
      fireEvent.click(saveButton)
    })
    expect(saveButton).not.toBeDisabled()
    expect(saveButton).toHaveAttribute('aria-disabled', 'true')
    expect(saveButton).toHaveAttribute('aria-busy', 'true')
    expect(saveButton).toHaveFocus()
    expect(mockSaveEpisodeLabels).toHaveBeenCalledOnce()
    testState.episodeLabels = { 0: ['PARTIAL'] }
    rerender(<AnnotationWorkspace />)
    await act(async () => {
      finish()
      await pending
    })
    expect(screen.getByRole('status')).toHaveTextContent(/unsaved episode changes/i)
    expect(testState.episodeLabels[0]).toEqual(['PARTIAL'])
    expect(testState.savedEpisodeLabels[0]).toEqual(['FAILURE'])
  })

  it('offers separate Save and Next actions without saving when Next is selected', async () => {
    const next = vi.fn()
    render(<AnnotationWorkspace canGoNextEpisode onNextEpisode={next} />)
    expect(screen.getByRole('button', { name: /^save episode$/i })).toBeEnabled()
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^next episode$/i }))
    })
    expect(next).toHaveBeenCalledOnce()
    expect(mockSaveEpisodeLabels).not.toHaveBeenCalled()
  })

  it('announces saved-edit load errors without hiding the current workspace', () => {
    testState.editPersistenceError = 'Saved edits could not be loaded.'
    render(<AnnotationWorkspace />)
    expect(screen.getByRole('alert')).toHaveTextContent('Saved edits could not be loaded.')
    expect(screen.getByRole('heading', { name: /episode 0/i })).toBeVisible()
  })

  it('keeps the save status hidden until a save occurs', () => {
    render(<AnnotationWorkspace />)

    expect(screen.queryByText(/changes save automatically/i)).not.toBeInTheDocument()
  })

  it('shows pending episode changes instead of auto-save copy after labels change locally', () => {
    const { rerender } = render(<AnnotationWorkspace />)

    fireEvent.mouseDown(screen.getByRole('tab', { name: /trajectory viewer/i }), {
      button: 0,
      ctrlKey: false,
    })
    fireEvent.click(screen.getByRole('button', { name: /toggle label draft/i }))
    rerender(<AnnotationWorkspace />)

    const actions = screen.getByTestId('workspace-header-actions')
    expect(within(actions).getByText(/unsaved episode changes/i)).toBeInTheDocument()
    expect(screen.queryByText(/changes save automatically/i)).not.toBeInTheDocument()
    expect(mockSaveEpisodeLabels).not.toHaveBeenCalled()
  })

  it('shows a saved message after standalone Save and hides it after a short delay', async () => {
    const handleSaveAndNextEpisode = vi.fn()
    const { rerender } = render(
      <AnnotationWorkspace canGoNextEpisode onSaveAndNextEpisode={handleSaveAndNextEpisode} />,
    )

    testState.episodeLabels = { 0: ['FAILURE'] }
    rerender(
      <AnnotationWorkspace canGoNextEpisode onSaveAndNextEpisode={handleSaveAndNextEpisode} />,
    )

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^save episode$/i }))
      await Promise.resolve()
    })

    expect(screen.getByRole('status')).toHaveTextContent(/episode changes saved/i)

    act(() => {
      vi.advanceTimersByTime(2500)
    })

    expect(screen.queryByText(/episode changes saved/i)).not.toBeInTheDocument()
  })

  it('does not show stale unsaved changes after saving and separately navigating', async () => {
    const handleSaveAndNextEpisode = vi.fn(() => {
      testState.episodeIndex = 1
      testState.episodeLabels = { ...testState.episodeLabels, 1: [] }
      testState.savedEpisodeLabels = { ...testState.savedEpisodeLabels, 1: [] }
    })
    const { rerender } = render(
      <AnnotationWorkspace canGoNextEpisode onNextEpisode={handleSaveAndNextEpisode} />,
    )

    testState.episodeLabels = { 0: ['FAILURE'], 1: [] }
    testState.savedEpisodeLabels = { 0: ['SUCCESS'], 1: [] }
    rerender(<AnnotationWorkspace canGoNextEpisode onNextEpisode={handleSaveAndNextEpisode} />)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^save episode$/i }))
      await Promise.resolve()
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^next episode$/i }))
    })

    rerender(<AnnotationWorkspace canGoNextEpisode onNextEpisode={handleSaveAndNextEpisode} />)
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
    expect(
      screen.getByRole('heading', { name: /episode 0/i }).compareDocumentPosition(headerActions),
    ).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
    expect(headerActions.compareDocumentPosition(screen.getByRole('tablist'))).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
  })

  it('adds a dedicated trajectory viewer tab alongside the existing workspace tabs', () => {
    render(<AnnotationWorkspace />)

    expect(screen.queryByRole('tab', { name: /episode viewer/i })).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /trajectory viewer/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /episode analyzer/i })).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: /object detection/i })).not.toBeInTheDocument()
  })

  it('selects the trajectory viewer tab by default', () => {
    render(<AnnotationWorkspace />)

    expect(screen.getByRole('tab', { name: /trajectory viewer/i })).toHaveAttribute(
      'aria-selected',
      'true',
    )
  })

  it('renders a Previous Episode action in the workspace header when navigation is available', () => {
    const handlePreviousEpisode = vi.fn()

    render(<AnnotationWorkspace canGoPreviousEpisode onPreviousEpisode={handlePreviousEpisode} />)

    const previousEpisodeButton = screen.getByRole('button', { name: /previous episode/i })

    expect(previousEpisodeButton).toBeEnabled()
    fireEvent.click(previousEpisodeButton)
    expect(handlePreviousEpisode).toHaveBeenCalledTimes(1)
  })

  it('renders standalone Save even when navigation is available', async () => {
    const handleSaveAndNextEpisode = vi.fn()

    render(<AnnotationWorkspace canGoNextEpisode onSaveAndNextEpisode={handleSaveAndNextEpisode} />)

    const saveAndNextButton = within(screen.getByTestId('workspace-header-actions')).getByRole(
      'button',
      {
        name: /^save episode$/i,
      },
    )

    expect(saveAndNextButton).toBeEnabled()

    await act(async () => {
      fireEvent.click(saveAndNextButton)
      await Promise.resolve()
    })

    expect(handleSaveAndNextEpisode).not.toHaveBeenCalled()
  })

  it('saves labels without advancing when standalone Save is clicked', async () => {
    const handleSaveAndNextEpisode = vi.fn()
    const { rerender } = render(
      <AnnotationWorkspace canGoNextEpisode onSaveAndNextEpisode={handleSaveAndNextEpisode} />,
    )

    testState.episodeLabels = { 0: ['FAILURE'] }
    rerender(
      <AnnotationWorkspace canGoNextEpisode onSaveAndNextEpisode={handleSaveAndNextEpisode} />,
    )

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^save episode$/i }))
      await Promise.resolve()
    })

    expect(mockSaveEpisodeLabels).toHaveBeenCalledWith({ episodeIdx: 0, labels: ['FAILURE'] })
    expect(handleSaveAndNextEpisode).not.toHaveBeenCalled()
  })

  it('saves labels on the final episode without advancing', async () => {
    const handleSaveAndNextEpisode = vi.fn()
    testState.episodeLabels = { 0: [] }
    testState.savedEpisodeLabels = { 0: [] }
    const { rerender } = render(
      <AnnotationWorkspace onSaveAndNextEpisode={handleSaveAndNextEpisode} />,
    )

    testState.episodeLabels = { 0: ['SUCCESS'] }
    rerender(<AnnotationWorkspace onSaveAndNextEpisode={handleSaveAndNextEpisode} />)

    const saveButton = screen.getByRole('button', { name: /^save episode$/i })
    expect(saveButton).toBeEnabled()

    await act(async () => {
      fireEvent.click(saveButton)
      await Promise.resolve()
    })

    expect(mockSaveEpisodeLabels).toHaveBeenCalledWith({ episodeIdx: 0, labels: ['SUCCESS'] })
    expect(handleSaveAndNextEpisode).not.toHaveBeenCalled()
  })

  it('resets labels back to the original episode labels without saving when Reset All is clicked', async () => {
    const { rerender } = render(<AnnotationWorkspace />)

    testState.episodeLabels = { 0: ['FAILURE'] }
    rerender(<AnnotationWorkspace />)

    await act(async () => {
      fireEvent.click(
        within(screen.getByTestId('workspace-header-actions')).getByRole('button', {
          name: /^reset all$/i,
        }),
      )
      await Promise.resolve()
    })

    rerender(<AnnotationWorkspace />)

    expect(mockResetEdits).toHaveBeenCalled()
    expect(mockSaveEpisodeLabels).not.toHaveBeenCalled()
    expect(screen.queryByText(/unsaved episode changes/i)).not.toBeInTheDocument()
  })
})
