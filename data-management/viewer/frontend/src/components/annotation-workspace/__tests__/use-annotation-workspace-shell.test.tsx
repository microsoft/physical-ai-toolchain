import '@/components/__tests__/support/annotationWorkspaceTestSupport'

import { act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  mockInitializeEdit,
  mockRecordDiagnosticEvent,
  setupAnnotationWorkspaceTestCase,
  teardownAnnotationWorkspaceTestCase,
  testState,
} from '@/components/__tests__/support/annotationWorkspaceTestSupport'
import { useAnnotationWorkspaceShell } from '@/components/annotation-workspace/useAnnotationWorkspaceShell'
import { renderHookWithProviders } from '@/test-utils/render'
import { SUBTASK_COLORS } from '@/types/episode-edit'

describe('useAnnotationWorkspaceShell', () => {
  beforeEach(setupAnnotationWorkspaceTestCase)
  afterEach(teardownAnnotationWorkspaceTestCase)

  it('defaults the workspace shell to the trajectory tab', () => {
    const { result } = renderHookWithProviders(() => useAnnotationWorkspaceShell({}))

    expect(result.current.activeTab).toBe('trajectory')
  })

  it('records workspace diagnostics when switching tabs', () => {
    const { result } = renderHookWithProviders(() => useAnnotationWorkspaceShell({}))

    act(() => {
      result.current.handleTabChange('other')
    })

    expect(result.current.activeTab).toBe('other')
    expect(mockRecordDiagnosticEvent).toHaveBeenCalledWith('workspace', 'tab-change', {
      previousTab: 'trajectory',
      nextTab: 'other',
    })
  })

  it('opens the export dialog and records the export event', () => {
    const { result } = renderHookWithProviders(() => useAnnotationWorkspaceShell({}))

    act(() => {
      result.current.handleOpenExportDialog()
    })

    expect(result.current.exportDialogOpen).toBe(true)
    expect(mockRecordDiagnosticEvent).toHaveBeenCalledWith('export', 'dialog-open', {
      activeTab: 'trajectory',
      episodeIndex: 0,
    })
  })

  it('starts the edit store from the subtasks recorded with the episode', () => {
    testState.recordedSubtasks = [
      {
        id: 'recorded-0',
        label: 'Reach',
        frameRange: [0, 4],
        color: null,
        source: 'recorded',
        description: null,
      },
    ]

    renderHookWithProviders(() => useAnnotationWorkspaceShell({}))

    expect(mockInitializeEdit).toHaveBeenLastCalledWith('dataset-1', 0, 'principal-test', [
      {
        id: 'recorded-0',
        label: 'Reach',
        frameRange: [0, 4],
        color: SUBTASK_COLORS[0],
        source: 'recorded',
      },
    ])
  })

  it('starts the edit store with no subtasks when the episode records none', () => {
    testState.recordedSubtasks = undefined

    renderHookWithProviders(() => useAnnotationWorkspaceShell({}))

    expect(mockInitializeEdit).toHaveBeenLastCalledWith('dataset-1', 0, 'principal-test', [])
  })
})
