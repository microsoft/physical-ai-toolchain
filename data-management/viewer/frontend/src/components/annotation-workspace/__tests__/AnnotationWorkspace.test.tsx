import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AnnotationWorkspace } from '@/components/annotation-workspace/AnnotationWorkspace'
import type { useAnnotationWorkspaceShell } from '@/components/annotation-workspace/useAnnotationWorkspaceShell'

type Shell = ReturnType<typeof useAnnotationWorkspaceShell>

let mockShellResult: Shell

vi.mock('@/components/annotation-workspace/useAnnotationWorkspaceShell', () => ({
  useAnnotationWorkspaceShell: () => mockShellResult,
}))

vi.mock('@/components/annotation-workspace/AnnotationWorkspaceContent', () => ({
  AnnotationWorkspaceContent: () => <div data-testid="content-stub">CONTENT_STUB</div>,
}))

vi.mock('@/components/annotation-workspace/AnnotationWorkspaceEmptyState', () => ({
  AnnotationWorkspaceEmptyState: () => <div data-testid="empty-stub">EMPTY_STUB</div>,
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
})
