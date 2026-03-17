import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { useAnnotationWorkspaceEpisodeActions } from '@/components/annotation-workspace/useAnnotationWorkspaceEpisodeActions'

describe('useAnnotationWorkspaceEpisodeActions', () => {
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
})
