import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { useAnnotationWorkspaceEpisodeActions } from '@/components/annotation-workspace/useAnnotationWorkspaceEpisodeActions'

describe('useAnnotationWorkspaceEpisodeActions', () => {
  it('saves all resources once without navigating from standalone Save', async () => {
    let complete!: () => void
    const pending = new Promise<void>((resolve) => {
      complete = resolve
    })
    const annotations = vi.fn(() => pending)
    const labels = vi.fn().mockResolvedValue(undefined)
    const edits = vi.fn().mockResolvedValue(undefined)
    const advance = vi.fn()
    const { result } = renderHook(() =>
      useAnnotationWorkspaceEpisodeActions({
        diagnosticsEnabled: true,
        currentDatasetId: 'dataset-1',
        currentEpisodeIndex: 0,
        currentEpisodeLabels: ['SUCCESS'],
        savedLabelsForCurrentEpisode: [],
        availableLabels: ['SUCCESS'],
        labelDataLoaded: true,
        hasEdits: true,
        hasAnnotationChanges: true,
        onResetEdits: vi.fn(),
        onSetEpisodeLabels: vi.fn(),
        onSaveEpisodeDraft: edits,
        onSaveEpisodeLabels: labels,
        onSaveEpisodeAnnotation: annotations,
        onRecordEvent: vi.fn(),
        canGoNextEpisode: true,
        onAdvanceToNextEpisode: advance,
      }),
    )
    let first!: Promise<void>
    act(() => {
      first = result.current.handleSaveEpisode()
    })
    await act(async () => {
      await result.current.handleSaveEpisode()
    })
    expect(result.current.isSaving).toBe(true)
    expect(annotations).toHaveBeenCalledOnce()
    expect(labels).toHaveBeenCalledOnce()
    expect(edits).toHaveBeenCalledOnce()
    await act(async () => {
      complete()
      await first
    })
    expect(result.current.isSaving).toBe(false)
    expect(advance).not.toHaveBeenCalled()
  })

  it('waits for durable edit acknowledgment before advancing', async () => {
    let completeSave!: () => void
    const save = new Promise<void>((resolve) => {
      completeSave = resolve
    })
    const advance = vi.fn()
    const { result } = renderHook(() =>
      useAnnotationWorkspaceEpisodeActions({
        diagnosticsEnabled: true,
        currentDatasetId: 'dataset-1',
        currentEpisodeIndex: 0,
        currentEpisodeLabels: [],
        savedLabelsForCurrentEpisode: [],
        availableLabels: [],
        labelDataLoaded: true,
        hasEdits: true,
        onResetEdits: vi.fn(),
        onSetEpisodeLabels: vi.fn(),
        onSaveEpisodeDraft: () => save,
        onSaveEpisodeLabels: vi.fn(),
        onRecordEvent: vi.fn(),
        canGoNextEpisode: true,
        onAdvanceToNextEpisode: advance,
      }),
    )
    let pending!: Promise<void>
    act(() => {
      pending = result.current.handleSaveAndNextEpisode()
    })
    expect(advance).not.toHaveBeenCalled()
    await act(async () => {
      completeSave()
      await pending
    })
    expect(advance).toHaveBeenCalledOnce()
  })

  it('retains the episode and reports an edit-save failure', async () => {
    const advance = vi.fn()
    const { result } = renderHook(() =>
      useAnnotationWorkspaceEpisodeActions({
        diagnosticsEnabled: true,
        currentDatasetId: 'dataset-1',
        currentEpisodeIndex: 0,
        currentEpisodeLabels: [],
        savedLabelsForCurrentEpisode: [],
        availableLabels: [],
        labelDataLoaded: true,
        hasEdits: true,
        onResetEdits: vi.fn(),
        onSetEpisodeLabels: vi.fn(),
        onSaveEpisodeDraft: () => {
          throw new Error('unavailable')
        },
        onSaveEpisodeLabels: vi.fn(),
        onRecordEvent: vi.fn(),
        canGoNextEpisode: true,
        onAdvanceToNextEpisode: advance,
      }),
    )
    await act(async () => {
      await result.current.handleSaveAndNextEpisode()
    })
    expect(advance).not.toHaveBeenCalled()
    expect(result.current.saveStatusMessage).toMatch(/could not be saved/i)
  })

  it('restores saved labels that are still available when reset-all runs', async () => {
    const handleResetEdits = vi.fn()
    const handleSetEpisodeLabels = vi.fn()

    const { result } = renderHook(() =>
      useAnnotationWorkspaceEpisodeActions({
        diagnosticsEnabled: false,
        currentDatasetId: 'dataset-1',
        currentEpisodeIndex: 2,
        currentEpisodeLabels: ['keep', 'drop'],
        savedLabelsForCurrentEpisode: ['keep', 'missing'],
        availableLabels: ['keep', 'other'],
        labelDataLoaded: true,
        hasEdits: true,
        onResetEdits: handleResetEdits,
        onSetEpisodeLabels: handleSetEpisodeLabels,
        onSaveEpisodeDraft: vi.fn(),
        onSaveEpisodeLabels: vi.fn(),
        onRecordEvent: vi.fn(),
        canGoNextEpisode: false,
      }),
    )

    expect(result.current.hasLabelChanges).toBe(true)
    expect(result.current.hasPendingEpisodeChanges).toBe(true)

    await act(async () => {
      await result.current.handleResetAll()
    })

    expect(handleResetEdits).toHaveBeenCalledOnce()
    expect(handleSetEpisodeLabels).toHaveBeenCalledWith(2, ['keep'])
  })

  it('saves labels and draft edits before advancing to the next episode', async () => {
    const handleSaveEpisodeLabels = vi.fn().mockResolvedValue(undefined)
    const handleSaveEpisodeDraft = vi.fn()
    const handleAdvance = vi.fn()
    const handleRecordEvent = vi.fn()

    const { result } = renderHook(() =>
      useAnnotationWorkspaceEpisodeActions({
        diagnosticsEnabled: true,
        currentDatasetId: 'dataset-1',
        currentEpisodeIndex: 4,
        currentEpisodeLabels: ['success'],
        savedLabelsForCurrentEpisode: [],
        availableLabels: ['success'],
        labelDataLoaded: true,
        hasEdits: true,
        onResetEdits: vi.fn(),
        onSetEpisodeLabels: vi.fn(),
        onSaveEpisodeDraft: handleSaveEpisodeDraft,
        onSaveEpisodeLabels: handleSaveEpisodeLabels,
        onRecordEvent: handleRecordEvent,
        canGoNextEpisode: true,
        onAdvanceToNextEpisode: handleAdvance,
      }),
    )

    await act(async () => {
      await result.current.handleSaveAndNextEpisode()
    })

    expect(handleSaveEpisodeLabels).toHaveBeenCalledWith({ episodeIdx: 4, labels: ['success'] })
    expect(handleSaveEpisodeDraft).toHaveBeenCalledOnce()
    expect(handleAdvance).toHaveBeenCalledOnce()
    expect(handleRecordEvent).toHaveBeenCalledWith(
      'workspace',
      'save-next-episode',
      expect.objectContaining({
        episodeIndex: 4,
        hasEdits: true,
        hasLabelChanges: true,
        hasPendingEpisodeChanges: true,
      }),
    )
  })
  it('does not reset episode changes when the user cancels confirmation', async () => {
    const confirmSpy = vi.fn(() => false)
    vi.stubGlobal('confirm', confirmSpy)
    const onResetEdits = vi.fn()
    const onSetEpisodeLabels = vi.fn()
    const { result } = renderHook(() =>
      useAnnotationWorkspaceEpisodeActions({
        diagnosticsEnabled: false,
        currentDatasetId: 'dataset-1',
        currentEpisodeIndex: 2,
        currentEpisodeLabels: ['changed'],
        savedLabelsForCurrentEpisode: ['saved'],
        availableLabels: ['changed', 'saved'],
        labelDataLoaded: true,
        hasEdits: true,
        onResetEdits,
        onSetEpisodeLabels,
        onSaveEpisodeDraft: vi.fn(),
        onSaveEpisodeLabels: vi.fn(),
        onRecordEvent: vi.fn(),
        canGoNextEpisode: false,
      }),
    )

    await act(async () => result.current.handleResetAll())

    expect(confirmSpy).toHaveBeenCalledWith(
      'Discard unsaved annotation, label, and frame-edit changes for this episode?',
    )
    expect(onResetEdits).not.toHaveBeenCalled()
    expect(onSetEpisodeLabels).not.toHaveBeenCalled()
  })
})
