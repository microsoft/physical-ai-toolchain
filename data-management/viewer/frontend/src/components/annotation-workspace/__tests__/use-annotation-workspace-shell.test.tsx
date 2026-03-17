import '@/components/__tests__/support/annotationWorkspaceTestSupport'

import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  mockRecordDiagnosticEvent,
  setupAnnotationWorkspaceTestCase,
  teardownAnnotationWorkspaceTestCase,
} from '@/components/__tests__/support/annotationWorkspaceTestSupport'
import { useAnnotationWorkspaceShell } from '@/components/annotation-workspace/useAnnotationWorkspaceShell'

describe('useAnnotationWorkspaceShell', () => {
  beforeEach(setupAnnotationWorkspaceTestCase)
  afterEach(teardownAnnotationWorkspaceTestCase)

  it('defaults the workspace shell to the trajectory tab', () => {
    const { result } = renderHook(() => useAnnotationWorkspaceShell({}))

    expect(result.current.activeTab).toBe('trajectory')
  })

  it('records workspace and detection diagnostics when switching to the detection tab', () => {
    const { result } = renderHook(() => useAnnotationWorkspaceShell({}))

    act(() => {
      result.current.handleTabChange('detection')
    })

    expect(result.current.activeTab).toBe('detection')
    expect(mockRecordDiagnosticEvent).toHaveBeenCalledWith('workspace', 'tab-change', {
      previousTab: 'trajectory',
      nextTab: 'detection',
    })
    expect(mockRecordDiagnosticEvent).toHaveBeenCalledWith('detection', 'tab-viewed', {
      previousTab: 'trajectory',
      episodeIndex: 0,
    })
  })

  it('opens the export dialog and records the export event', () => {
    const { result } = renderHook(() => useAnnotationWorkspaceShell({}))

    act(() => {
      result.current.handleOpenExportDialog()
    })

    expect(result.current.exportDialogOpen).toBe(true)
    expect(mockRecordDiagnosticEvent).toHaveBeenCalledWith('export', 'dialog-open', {
      activeTab: 'trajectory',
      episodeIndex: 0,
    })
  })
})
