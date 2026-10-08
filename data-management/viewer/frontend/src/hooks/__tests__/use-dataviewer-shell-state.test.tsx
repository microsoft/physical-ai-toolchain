import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useDataviewerShellState } from '@/hooks/use-dataviewer-shell-state'
import { useAnnotationStore, useDatasetStore, useEditStore, useLabelStore } from '@/stores'
import type { DatasetInfo } from '@/types'

const { mockEnableDiagnostics, mockDisableDiagnostics, mockIsDiagnosticsEnabled } = vi.hoisted(
  () => ({
    mockEnableDiagnostics: vi.fn(),
    mockDisableDiagnostics: vi.fn(),
    mockIsDiagnosticsEnabled: vi.fn(() => false),
  }),
)

vi.mock('@/lib/playback-diagnostics', () => ({
  enableDiagnostics: mockEnableDiagnostics,
  disableDiagnostics: mockDisableDiagnostics,
  isDiagnosticsEnabled: mockIsDiagnosticsEnabled,
}))

vi.mock('@/lib/api-client', () => ({
  warmCache: vi.fn().mockResolvedValue(undefined),
}))

describe('useDataviewerShellState', () => {
  const datasets: DatasetInfo[] = [
    {
      id: 'dataset-a',
      name: 'Dataset A',
      totalEpisodes: 4,
      fps: 30,
      features: {},
      tasks: [],
    },
    {
      id: 'dataset-b',
      name: 'Dataset B',
      totalEpisodes: 2,
      fps: 30,
      features: {},
      tasks: [],
    },
  ]

  beforeEach(() => {
    useDatasetStore.getState().reset()
    useAnnotationStore.getState().clear()
    useEditStore.getState().clear()
    useLabelStore.getState().reset()
    mockEnableDiagnostics.mockClear()
    mockDisableDiagnostics.mockClear()
    mockIsDiagnosticsEnabled.mockReturnValue(false)
  })

  afterEach(() => vi.unstubAllGlobals())

  it('protects every navigation entry point while annotations are dirty', async () => {
    const confirm = vi.fn(() => false)
    vi.stubGlobal('confirm', confirm)
    const { result } = renderHook(() => useDataviewerShellState({ datasets }))
    await waitFor(() => expect(result.current.isWarmingCache).toBe(false))
    act(() => result.current.setSelectedEpisode(1))
    act(() => {
      useAnnotationStore.getState().initializeAnnotation('principal-one')
      useAnnotationStore.getState().updateNotes('Unsaved')
    })
    act(() => result.current.handlePreviousEpisode())
    expect(result.current.selectedEpisode).toBe(1)
    act(() => result.current.handleNextEpisode())
    expect(result.current.selectedEpisode).toBe(1)
    act(() => result.current.setSelectedEpisode(3))
    expect(result.current.selectedEpisode).toBe(1)
    act(() => result.current.setDatasetId('dataset-b'))
    expect(result.current.datasetId).toBe('dataset-a')
    expect(confirm).toHaveBeenCalledTimes(4)
    expect(useAnnotationStore.getState().currentAnnotation?.notes).toBe('Unsaved')
  })

  it('warns before browser unload with dirty labels and removes the listener on unmount', async () => {
    const { result, unmount } = renderHook(() => useDataviewerShellState({ datasets }))
    await waitFor(() => expect(result.current.isWarmingCache).toBe(false))
    act(() => useLabelStore.getState().setEpisodeLabels(0, ['FAILURE']))
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    unmount()
    const after = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(after)
    expect(after.defaultPrevented).toBe(false)
  })

  it('selects the first available dataset and keeps the dataset store in sync', async () => {
    const { result } = renderHook(() =>
      useDataviewerShellState({
        datasets,
        episodes: [
          { index: 0, length: 12, taskIndex: 0, hasAnnotations: false },
          { index: 1, length: 10, taskIndex: 0, hasAnnotations: false },
          { index: 2, length: 8, taskIndex: 0, hasAnnotations: false },
        ],
      }),
    )

    expect(result.current.datasetId).toBe('dataset-a')
    expect(useDatasetStore.getState().datasets).toHaveLength(2)
    expect(useDatasetStore.getState().currentDataset?.id).toBe('dataset-a')
    expect(result.current.canGoPreviousEpisode).toBe(false)
    expect(result.current.canGoNextEpisode).toBe(true)

    await waitFor(() => expect(result.current.isWarmingCache).toBe(false))
  })

  it('resets to the next available dataset when the selected dataset disappears and toggles diagnostics', async () => {
    const { result, rerender } = renderHook(
      ({ nextDatasets }) =>
        useDataviewerShellState({
          datasets: nextDatasets,
          episodes: [
            { index: 0, length: 12, taskIndex: 0, hasAnnotations: false },
            { index: 1, length: 10, taskIndex: 0, hasAnnotations: false },
          ],
        }),
      { initialProps: { nextDatasets: datasets } },
    )

    await waitFor(() => expect(result.current.isWarmingCache).toBe(false))

    await act(async () => {
      result.current.setDatasetId('dataset-b')
      result.current.setSelectedEpisode(1)
      result.current.toggleDiagnostics()
    })

    expect(mockEnableDiagnostics).toHaveBeenCalledOnce()
    await waitFor(() => expect(result.current.isWarmingCache).toBe(false))

    await act(async () => {
      rerender({
        nextDatasets: [datasets[0]],
      })
    })

    await waitFor(() => expect(result.current.datasetId).toBe('dataset-a'))
    expect(result.current.selectedEpisode).toBe(0)

    act(() => {
      result.current.toggleDiagnostics()
    })

    expect(mockDisableDiagnostics).toHaveBeenCalledOnce()
  })
})
