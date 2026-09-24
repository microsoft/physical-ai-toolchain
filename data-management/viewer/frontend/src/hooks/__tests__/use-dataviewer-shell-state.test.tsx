import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useDataviewerShellState } from '@/hooks/use-dataviewer-shell-state'
import { useDatasetStore } from '@/stores'
import type { DatasetInfo } from '@/types'

const { mockEnableDiagnostics, mockDisableDiagnostics, mockIsDiagnosticsEnabled, mockWarmCache } =
  vi.hoisted(() => ({
    mockEnableDiagnostics: vi.fn(),
    mockDisableDiagnostics: vi.fn(),
    mockIsDiagnosticsEnabled: vi.fn(() => false),
    mockWarmCache: vi.fn().mockResolvedValue(undefined),
  }))

vi.mock('@/lib/playback-diagnostics', () => ({
  enableDiagnostics: mockEnableDiagnostics,
  disableDiagnostics: mockDisableDiagnostics,
  isDiagnosticsEnabled: mockIsDiagnosticsEnabled,
}))

vi.mock('@/lib/api-client', () => ({
  warmCache: mockWarmCache,
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
    mockEnableDiagnostics.mockClear()
    mockDisableDiagnostics.mockClear()
    mockIsDiagnosticsEnabled.mockReturnValue(false)
    mockWarmCache.mockClear()
  })

  it('does not warm the cache when a read-only release is selected automatically', async () => {
    const releaseDataset: DatasetInfo = {
      ...datasets[0],
      id: 'release-job-123',
      isReadOnly: true,
    }

    const { result } = renderHook(() => useDataviewerShellState({ datasets: [releaseDataset] }))

    await waitFor(() => expect(result.current.datasetId).toBe(releaseDataset.id))
    expect(mockWarmCache).not.toHaveBeenCalled()
  })

  it('does not warm the cache when switching to a read-only release', async () => {
    const releaseDataset: DatasetInfo = {
      ...datasets[1],
      id: 'release-job-123',
      isReadOnly: true,
    }
    const { result } = renderHook(() =>
      useDataviewerShellState({ datasets: [datasets[0], releaseDataset] }),
    )
    await waitFor(() => expect(mockWarmCache).toHaveBeenCalledWith(datasets[0].id, 5))
    mockWarmCache.mockClear()

    act(() => result.current.setDatasetId(releaseDataset.id))

    expect(mockWarmCache).not.toHaveBeenCalled()
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
