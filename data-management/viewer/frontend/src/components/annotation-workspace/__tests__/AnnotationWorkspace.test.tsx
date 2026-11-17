import { fireEvent, render, screen } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AnnotationWorkspace } from '@/components/annotation-workspace/AnnotationWorkspace'
import type { useAnnotationWorkspaceShell } from '@/components/annotation-workspace/useAnnotationWorkspaceShell'

type Shell = ReturnType<typeof useAnnotationWorkspaceShell>

let mockShellResult: Shell
const visualEffect = vi.hoisted(() => vi.fn())

vi.mock('@/components/annotation-workspace/useAnnotationWorkspaceShell', () => ({
  useAnnotationWorkspaceShell: () => mockShellResult,
}))

vi.mock('@/components/annotation-workspace/AnnotationWorkspaceContent', () => ({
  AnnotationWorkspaceContent: function VisualContentProbe() {
    const [draft, setDraft] = useState('')
    useEffect(() => {
      visualEffect('active')
      return () => visualEffect('inactive')
    }, [])
    return (
      <div data-testid="content-stub">
        CONTENT_STUB
        <input
          aria-label="Visual draft"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
      </div>
    )
  },
}))

vi.mock('@/components/annotation-workspace/AnnotationWorkspaceEmptyState', () => ({
  AnnotationWorkspaceEmptyState: () => <div data-testid="empty-stub">EMPTY_STUB</div>,
}))

vi.mock('@/components/annotation-workspace/DraftConflictDialog', () => ({
  DraftConflictDialog: () => null,
}))

describe('AnnotationWorkspace', () => {
  beforeEach(() => {
    mockShellResult = { currentDataset: null, currentEpisode: null } as unknown as Shell
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the empty state when no dataset or episode is selected', () => {
    render(<AnnotationWorkspace />)

    expect(screen.getByTestId('empty-stub')).toBeInTheDocument()
    expect(screen.queryByTestId('content-stub')).not.toBeInTheDocument()
  })

  it('renders the workspace content when a dataset and episode are loaded', () => {
    mockShellResult = {
      currentDataset: { id: 'ds-1' },
      currentEpisode: { meta: { index: 0 } },
    } as unknown as Shell

    render(<AnnotationWorkspace />)

    expect(screen.getByTestId('content-stub')).toBeInTheDocument()
    expect(screen.queryByTestId('empty-stub')).not.toBeInTheDocument()
  })

  it('suspends hidden visual effects and restores their draft state', () => {
    mockShellResult = {
      currentDataset: { id: 'ds-1' },
      currentEpisode: { meta: { index: 0 } },
    } as unknown as Shell
    const { rerender } = render(<AnnotationWorkspace />)
    fireEvent.change(screen.getByLabelText('Visual draft'), { target: { value: 'retained draft' } })
    visualEffect.mockClear()

    rerender(<AnnotationWorkspace visible={false} />)

    expect(visualEffect).toHaveBeenLastCalledWith('inactive')
    expect(screen.getByTestId('content-stub')).not.toBeVisible()

    rerender(<AnnotationWorkspace />)

    expect(visualEffect).toHaveBeenLastCalledWith('active')
    expect(screen.getByLabelText('Visual draft')).toHaveValue('retained draft')
  })
})
