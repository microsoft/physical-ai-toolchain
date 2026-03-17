import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useDataviewerShellState } from '@/hooks/use-dataviewer-shell-state'
import { useDatasetStore } from '@/stores'
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
    mockEnableDiagnostics.mockClear()
    mockDisableDiagnostics.mockClear()
    mockIsDiagnosticsEnabled.mockReturnValue(false)
  })

  it('selects the first available dataset and keeps the dataset store in sync', () => {
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
  })

  it('resets to the next available dataset when the selected dataset disappears and toggles diagnostics', () => {
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

    act(() => {
      result.current.setDatasetId('dataset-b')
      result.current.setSelectedEpisode(1)
      result.current.toggleDiagnostics()
    })

    expect(mockEnableDiagnostics).toHaveBeenCalledOnce()

    rerender({
      nextDatasets: [datasets[0]],
    })

    expect(result.current.datasetId).toBe('dataset-a')
    expect(result.current.selectedEpisode).toBe(0)

    act(() => {
      result.current.toggleDiagnostics()
    })

    expect(mockDisableDiagnostics).toHaveBeenCalledOnce()
  })
})
