import '@/components/__tests__/support/annotationWorkspaceTestSupport'

import { act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  mockRecordDiagnosticEvent,
  mockSaveAnnotation,
  mockSaveEpisodeDraft,
  mockTogglePlayback,
  setupAnnotationWorkspaceTestCase,
  teardownAnnotationWorkspaceTestCase,
  testState,
} from '@/components/__tests__/support/annotationWorkspaceTestSupport'
import { useAnnotationWorkspaceShell } from '@/components/annotation-workspace/useAnnotationWorkspaceShell'
import * as annotationHooks from '@/hooks/use-annotations'
import { useEpisodeEdits } from '@/hooks/use-episode-edits'
import * as principalHooks from '@/hooks/use-principal-context'
import { ApiClientError } from '@/lib/api-client'
import * as stores from '@/stores'
import { renderHookWithProviders } from '@/test-utils/render'
import { SUBTASK_COLORS } from '@/types/episode-edit'

describe('useAnnotationWorkspaceShell', () => {
  beforeEach(setupAnnotationWorkspaceTestCase)
  afterEach(() => {
    teardownAnnotationWorkspaceTestCase()
    vi.restoreAllMocks()
  })

  it.each(['pending', 'failed'])(
    'blocks workspace saves while identity is %s',
    async (identity) => {
      testState.hasAnnotationChanges = true
      vi.spyOn(principalHooks, 'usePrincipalContext').mockReturnValue({
        data: identity === 'pending' ? undefined : { scopeId: 'principal-test', authMode: 'local' },
        error: identity === 'failed' ? new Error('Identity unavailable') : null,
      } as ReturnType<typeof principalHooks.usePrincipalContext>)
      const { result } = renderHookWithProviders(() => useAnnotationWorkspaceShell({}))

      await act(async () => {
        await result.current.handleSaveEpisode()
      })

      expect(result.current.saveStatusMessage).toBe('Reload your identity before saving.')
      expect(mockSaveAnnotation).not.toHaveBeenCalled()
      expect(mockSaveEpisodeDraft).not.toHaveBeenCalled()
    },
  )

  it.each([401, 403, 404])(
    'blocks workspace saves after annotation access error %s',
    async (status) => {
      testState.hasAnnotationChanges = true
      const useEpisodeAnnotations = annotationHooks.useEpisodeAnnotations
      vi.spyOn(annotationHooks, 'useEpisodeAnnotations').mockImplementation((...args) => ({
        ...useEpisodeAnnotations(...args),
        data: undefined,
        error: new ApiClientError('Access unavailable', 'ACCESS_UNAVAILABLE', status),
        status: 'error',
        isError: true,
        isLoadingError: true,
        isRefetchError: false,
        isPending: false,
        isLoading: false,
        isSuccess: false,
        isPlaceholderData: false,
      }))
      const { result } = renderHookWithProviders(() => useAnnotationWorkspaceShell({}))

      await act(async () => {
        await result.current.handleSaveEpisode()
      })

      expect(result.current.saveStatusMessage).toBe(
        'Annotation access is unavailable. Reload before saving.',
      )
      expect(mockSaveAnnotation).not.toHaveBeenCalled()
      expect(testState.hasAnnotationChanges).toBe(true)
    },
  )

  it('stops playback when the workspace becomes hidden', () => {
    testState.isPlaying = true
    const { rerender } = renderHookWithProviders(
      ({ visible }) => useAnnotationWorkspaceShell({ visible }),
      {
        initialProps: { visible: true },
      },
    )
    expect(mockTogglePlayback).not.toHaveBeenCalled()

    rerender({ visible: false })

    expect(mockTogglePlayback).toHaveBeenCalledOnce()
  })

  it.each([
    {
      state: { annotationRecoveryError: 'Annotation recovery failed' },
      message: 'Annotation recovery failed',
    },
    { state: { labelRecoveryError: 'Label recovery failed' }, message: 'Label recovery failed' },
    { state: { editPersistenceError: 'Edit recovery failed' }, message: 'Edit recovery failed' },
    { state: { labelDraftHydrated: false }, message: 'Loading label drafts.' },
    { state: { labelsLoaded: false }, message: 'Loading labels.' },
  ])('preserves pending edits and blocks save: $message', async ({ state, message }) => {
    Object.assign(testState, state, { hasAnnotationChanges: true, hasEdits: true })
    const { result } = renderHookWithProviders(() => useAnnotationWorkspaceShell({}))

    await act(async () => {
      await result.current.handleSaveEpisode()
    })

    expect(result.current.canSaveEpisode).toBe(false)
    expect(result.current.saveStatusMessage).toBe(message)
    expect(mockSaveAnnotation).not.toHaveBeenCalled()
    expect(mockSaveEpisodeDraft).not.toHaveBeenCalled()
    expect(testState.hasAnnotationChanges).toBe(true)
    expect(testState.hasEdits).toBe(true)
  })

  it('defaults the workspace shell to the trajectory tab', () => {
    const { result } = renderHookWithProviders(() => useAnnotationWorkspaceShell({}))

    expect(result.current.activeTab).toBe('trajectory')
  })

  it('disables save while no episode is loaded', async () => {
    vi.spyOn(stores, 'useEpisodeStore').mockImplementation((selector) =>
      selector({ currentEpisode: null } as ReturnType<typeof stores.useEpisodeStore.getState>),
    )
    const { result } = renderHookWithProviders(() => useAnnotationWorkspaceShell({}))

    await act(async () => {
      await result.current.handleSaveEpisode()
    })

    expect(result.current.canSaveEpisode).toBe(false)
    expect(mockSaveAnnotation).not.toHaveBeenCalled()
    expect(mockSaveEpisodeDraft).not.toHaveBeenCalled()
  })

  it('blocks saves until the saved-edit revision is ready', async () => {
    testState.hasEdits = true
    vi.mocked(useEpisodeEdits).mockReturnValue({
      isReady: false,
      persistenceError: null,
    } as ReturnType<typeof useEpisodeEdits>)
    const { result } = renderHookWithProviders(() => useAnnotationWorkspaceShell({}))

    await act(async () => {
      await result.current.handleSaveEpisode()
    })

    expect(result.current.saveStatusMessage).toBe('Loading saved edits.')
    expect(mockSaveEpisodeDraft).not.toHaveBeenCalled()
    expect(testState.hasEdits).toBe(true)
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

  it('passes recorded subtasks to scoped edit recovery', () => {
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

    expect(useEpisodeEdits).toHaveBeenLastCalledWith('dataset-1', 0, 'principal-test', [
      {
        id: 'recorded-0',
        label: 'Reach',
        frameRange: [0, 4],
        color: SUBTASK_COLORS[0],
        source: 'recorded',
      },
    ])
  })

  it('passes no recorded subtasks to scoped edit recovery when the episode records none', () => {
    testState.recordedSubtasks = undefined

    renderHookWithProviders(() => useAnnotationWorkspaceShell({}))

    expect(useEpisodeEdits).toHaveBeenLastCalledWith('dataset-1', 0, 'principal-test', [])
  })
})
