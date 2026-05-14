import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { AnnotationWorkspaceContent } from '@/components/annotation-workspace/AnnotationWorkspaceContent'
import type { useAnnotationWorkspaceShell } from '@/components/annotation-workspace/useAnnotationWorkspaceShell'

type Shell = ReturnType<typeof useAnnotationWorkspaceShell>

vi.mock('@/components/annotation-panel', () => ({
  LabelPanel: () => null,
  LanguageInstructionWidget: () => null,
}))

vi.mock('@/components/annotation-workspace/AnnotationWorkspaceDiagnosticsPanel', () => ({
  AnnotationWorkspaceDiagnosticsPanel: () => null,
}))

vi.mock('@/components/annotation-workspace/AnnotationWorkspaceEditToolsPanel', () => ({
  AnnotationWorkspaceEditToolsPanel: () => null,
}))

vi.mock('@/components/annotation-workspace/AnnotationWorkspacePlaybackCard', () => ({
  AnnotationWorkspacePlaybackCard: () => null,
}))

vi.mock('@/components/annotation-workspace/AnnotationWorkspaceSubtaskListCard', () => ({
  AnnotationWorkspaceSubtaskListCard: () => null,
}))

vi.mock('@/components/annotation-workspace/AnnotationWorkspaceTopBar', () => ({
  AnnotationWorkspaceTopBar: () => null,
}))

vi.mock('@/components/annotation-workspace/AnnotationWorkspaceTrajectoryTab', () => ({
  AnnotationWorkspaceTrajectoryTab: () => null,
}))

vi.mock('@/components/export', () => ({ ExportDialog: () => null }))

vi.mock('@/components/object-detection', () => ({ DetectionPanel: () => null }))

describe('AnnotationWorkspaceContent guards', () => {
  it('renders null when currentDataset is missing', () => {
    const shell = {
      currentDataset: null,
      currentEpisode: { meta: { index: 0 } },
    } as unknown as Shell

    const { container } = render(<AnnotationWorkspaceContent shell={shell} />)

    expect(container.firstChild).toBeNull()
  })

  it('renders null when currentEpisode is missing', () => {
    const shell = { currentDataset: { id: 'ds' }, currentEpisode: null } as unknown as Shell

    const { container } = render(<AnnotationWorkspaceContent shell={shell} />)

    expect(container.firstChild).toBeNull()
  })
})
