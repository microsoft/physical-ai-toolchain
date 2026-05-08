import { act, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useCurrentEpisode,
  useEpisodeList,
  useEpisodeNavigationWithPrefetch,
} from '@/hooks/use-episodes'
import { _resetCsrfToken } from '@/lib/api-client'
import { useDatasetStore, useEpisodeStore } from '@/stores'
import { installFetchMock, jsonResponse, mockFetch } from '@/test-utils/fetch-mocks'
import { renderHookWithProviders } from '@/test-utils/render-hook'

const sampleDataset = {
  id: 'ds-1',
  name: 'Dataset 1',
  totalEpisodes: 3,
  fps: 30,
  features: {},
  tasks: [],
}

const sampleEpisodes = [
  { index: 0, length: 100, taskIndex: 0, hasAnnotations: false },
  { index: 1, length: 120, taskIndex: 0, hasAnnotations: false },
  { index: 2, length: 90, taskIndex: 0, hasAnnotations: true },
]

function selectDataset() {
  useDatasetStore.getState().setDatasets([sampleDataset])
  useDatasetStore.getState().selectDataset('ds-1')
}

beforeEach(() => {
  installFetchMock()
  _resetCsrfToken()
  useDatasetStore.getState().reset()
  useEpisodeStore.getState().reset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useEpisodeList', () => {
  it('does not fetch when no dataset is selected', () => {
    renderHookWithProviders(() => useEpisodeList())
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('fetches episodes for the selected dataset and syncs the store', async () => {
    selectDataset()
    mockFetch.mockResolvedValueOnce(
      jsonResponse([
        { index: 0, length: 100, task_index: 0, has_annotations: false },
        { index: 1, length: 120, task_index: 0, has_annotations: false },
      ]),
    )

    const { result } = renderHookWithProviders(() => useEpisodeList())

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(useEpisodeStore.getState().episodes).toEqual([
      { index: 0, length: 100, taskIndex: 0, hasAnnotations: false },
      { index: 1, length: 120, taskIndex: 0, hasAnnotations: false },
    ])
  })

  it('records errors in the store when the request fails', async () => {
    selectDataset()
    mockFetch.mockResolvedValueOnce(jsonResponse({ message: 'list failed', code: 'ERR' }, 500))

    const { result } = renderHookWithProviders(() => useEpisodeList())

    await waitFor(() => expect(result.current.isError).toBe(true))

    expect(useEpisodeStore.getState().error).toBe('list failed')
  })
})

describe('useCurrentEpisode', () => {
  it('is disabled when no dataset is selected', () => {
    renderHookWithProviders(() => useCurrentEpisode())
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('is disabled when currentIndex is negative', () => {
    selectDataset()
    renderHookWithProviders(() => useCurrentEpisode())
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('fetches the current episode and syncs it with the store', async () => {
    selectDataset()
    useEpisodeStore.getState().setEpisodes(sampleEpisodes)

    mockFetch.mockResolvedValue(
      jsonResponse({
        meta: { index: 1, length: 120, task_index: 0, has_annotations: false },
        video_urls: { front: 'http://example/1.mp4' },
        cameras: ['front'],
        trajectory_data: [],
      }),
    )

    act(() => {
      useEpisodeStore.getState().navigateToEpisode(1)
    })

    const { result } = renderHookWithProviders(() => useCurrentEpisode())

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const stored = useEpisodeStore.getState().currentEpisode
    expect(stored?.meta.index).toBe(1)
    expect(stored?.cameras).toEqual(['front'])
    expect(mockFetch).toHaveBeenCalledWith('/api/datasets/ds-1/episodes/1', expect.any(Object))
  })
})

describe('useEpisodeNavigationWithPrefetch', () => {
  it('reports navigation boundaries when the list is empty', () => {
    const { result } = renderHookWithProviders(() => useEpisodeNavigationWithPrefetch())

    expect(result.current.totalEpisodes).toBe(0)
    expect(result.current.canGoNext).toBe(false)
    expect(result.current.canGoPrevious).toBe(false)
  })

  it('disables previous when at the first episode and next when at the last', () => {
    useEpisodeStore.getState().setEpisodes(sampleEpisodes)

    const { result, rerender } = renderHookWithProviders(() => useEpisodeNavigationWithPrefetch())

    act(() => {
      useEpisodeStore.getState().navigateToEpisode(0)
    })
    rerender()
    expect(result.current.canGoPrevious).toBe(false)
    expect(result.current.canGoNext).toBe(true)

    act(() => {
      useEpisodeStore.getState().navigateToEpisode(2)
    })
    rerender()
    expect(result.current.canGoPrevious).toBe(true)
    expect(result.current.canGoNext).toBe(false)
  })

  it('exposes navigation actions wired to the episode store', () => {
    useEpisodeStore.getState().setEpisodes(sampleEpisodes)

    const { result } = renderHookWithProviders(() => useEpisodeNavigationWithPrefetch())

    act(() => {
      result.current.goToEpisode(0)
    })
    expect(useEpisodeStore.getState().currentIndex).toBe(0)

    act(() => {
      result.current.goNext()
    })
    expect(useEpisodeStore.getState().currentIndex).toBe(1)

    act(() => {
      result.current.goPrevious()
    })
    expect(useEpisodeStore.getState().currentIndex).toBe(0)
  })
})
