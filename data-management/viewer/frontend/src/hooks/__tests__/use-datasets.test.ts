import { focusManager } from '@tanstack/react-query'
import { act, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useCacheStats,
  useCapabilities,
  useDataset,
  useDatasetCatalog,
  useDatasets,
  useEpisode,
  useEpisodes,
} from '@/hooks/use-datasets'
import { useDatasetStore } from '@/stores'
import { installFetchMock, jsonResponse, mockFetch } from '@/test-utils/fetch-mocks'
import { createTestQueryClient, renderHookWithProviders } from '@/test-utils/render'

const sampleDataset = {
  id: 'ds-1',
  name: 'Dataset 1',
  totalEpisodes: 5,
  fps: 30,
  features: {},
  tasks: [],
}

beforeEach(() => {
  installFetchMock({ csrf: false })
  useDatasetStore.getState().reset()
})

afterEach(() => {
  focusManager.setFocused(undefined)
  vi.restoreAllMocks()
})

describe('useDatasets', () => {
  it('queries bounded catalog summaries without fetching episodes or full dataset detail', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ scope_id: 'principal-one', auth_mode: 'local' }))
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        items: [{ id: 'group--0999', name: 'Repeated', total_episodes: 999, format: 'lerobot' }],
        total: 1,
        catalog_total: 1000,
        groups: ['group'],
        snapshot_id: 'revision',
        offset: 0,
        limit: 25,
        stale: false,
        refresh_failed: false,
      }),
    )
    const { result } = renderHookWithProviders(() =>
      useDatasetCatalog({ query: '0999', limit: 25 }),
    )
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.items[0].totalEpisodes).toBe(999)
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/datasets/catalog?query=0999&limit=25',
      expect.any(Object),
    )
    expect(useDatasetStore.getState().currentDataset).toBeNull()
  })
  it('fetches the dataset list and syncs the store', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse([sampleDataset]))

    const { result } = renderHookWithProviders(() => useDatasets())

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data).toEqual([sampleDataset])
    expect(useDatasetStore.getState().datasets).toEqual([sampleDataset])
    expect(mockFetch).toHaveBeenCalledWith('/api/datasets', expect.any(Object))
  })

  it('records errors in the store when the request fails', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ message: 'boom', code: 'ERR' }, 500))

    const { result } = renderHookWithProviders(() => useDatasets())

    await waitFor(() => expect(result.current.isError).toBe(true))

    expect(useDatasetStore.getState().error).toBe('The server could not complete the request')
  })
})

describe('useDataset', () => {
  it('does not fetch when datasetId is undefined', () => {
    renderHookWithProviders(() => useDataset(undefined))
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('fetches a single dataset when an id is provided', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(sampleDataset))

    const { result } = renderHookWithProviders(() => useDataset('ds-1'))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data).toEqual(sampleDataset)
    expect(mockFetch).toHaveBeenCalledWith('/api/datasets/ds-1', expect.any(Object))
  })
})

describe('useEpisodes', () => {
  it('does not fetch when datasetId is undefined', () => {
    renderHookWithProviders(() => useEpisodes(undefined))
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('serializes options into the request URL and transforms the response', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse([
        { index: 0, length: 100, task_index: 0, has_annotations: false },
        { index: 1, length: 80, task_index: 1, has_annotations: true },
      ]),
    )

    const { result } = renderHookWithProviders(() =>
      useEpisodes('ds-1', { limit: 50, offset: 0, hasAnnotations: true, taskIndex: 2 }),
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data).toEqual([
      { index: 0, length: 100, taskIndex: 0, hasAnnotations: false },
      { index: 1, length: 80, taskIndex: 1, hasAnnotations: true },
    ])

    const calledUrl = mockFetch.mock.calls[0][0] as string
    expect(calledUrl).toContain('/api/datasets/ds-1/episodes?')
    expect(calledUrl).toContain('limit=50')
    expect(calledUrl).toContain('offset=0')
    expect(calledUrl).toContain('has_annotations=true')
    expect(calledUrl).toContain('task_index=2')
  })
})

describe('useEpisode', () => {
  it('cancels the old episode request when navigating', async () => {
    const queryClient = createTestQueryClient()
    queryClient.setQueryData(['auth', 'principal-context'], { scopeId: 'principal-one' })
    mockFetch.mockImplementationOnce(() => new Promise<Response>(() => {}))
    let index = 0
    const { result, rerender } = renderHookWithProviders(() => useEpisode('ds-1', index), {
      queryClient,
    })
    await waitFor(() => expect(mockFetch).toHaveBeenCalledOnce())
    const signal = mockFetch.mock.calls[0][1]?.signal
    expect(signal).toBeDefined()
    mockFetch.mockResolvedValueOnce(jsonResponse({ meta: { index: 1 } }))
    index = 1
    rerender()
    await waitFor(() => expect(result.current.data?.meta.index).toBe(1))
    expect(signal?.aborted).toBe(true)
  })

  it('does not refetch stale episode data on focus but honors explicit refresh', async () => {
    const queryClient = createTestQueryClient()
    queryClient.setQueryData(['auth', 'principal-context'], { scopeId: 'principal-one' })
    mockFetch.mockImplementation(() => Promise.resolve(jsonResponse({ meta: { index: 0 } })))
    const { result } = renderHookWithProviders(() => useEpisode('ds-1', 0), { queryClient })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['datasets'], refetchType: 'none' })
      focusManager.setFocused(false)
      focusManager.setFocused(true)
    })
    expect(mockFetch).toHaveBeenCalledOnce()
    await act(async () => {
      await result.current.refetch()
    })
    expect(mockFetch).toHaveBeenCalledTimes(2)
  })

  it('does not reuse the previous principal episode after an identity switch', async () => {
    const queryClient = createTestQueryClient()
    queryClient.setDefaultOptions({ queries: { gcTime: Infinity, retry: false } })
    queryClient.setQueryData(['auth', 'principal-context'], {
      scopeId: 'principal-one',
      authMode: 'local',
    })
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ meta: { index: 0, length: 10 }, cameras: ['front'] }),
    )
    const { result, rerender } = renderHookWithProviders(() => useEpisode('ds-1', 0), {
      queryClient,
    })
    await waitFor(() => expect(result.current.data?.cameras).toEqual(['front']))
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ meta: { index: 0, length: 10 }, cameras: ['wrist'] }),
    )
    act(() =>
      queryClient.setQueryData(['auth', 'principal-context'], {
        scopeId: 'principal-two',
        authMode: 'local',
      }),
    )
    rerender()
    await waitFor(() => expect(result.current.data?.cameras).toEqual(['wrist']))
    expect(mockFetch).toHaveBeenCalledTimes(2)
  })

  it('is disabled when datasetId is missing', () => {
    renderHookWithProviders(() => useEpisode(undefined, 0))
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('is disabled when episodeIndex is undefined', () => {
    renderHookWithProviders(() => useEpisode('ds-1', undefined))
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('is disabled when episodeIndex is negative', () => {
    renderHookWithProviders(() => useEpisode('ds-1', -1))
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('fetches the episode payload and applies key transforms', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ scope_id: 'principal-one', auth_mode: 'local' }))
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        meta: { index: 0, length: 100, task_index: 0, has_annotations: false },
        video_urls: { front: 'http://example/front.mp4' },
        cameras: ['front'],
        trajectory_data: [],
      }),
    )

    const { result } = renderHookWithProviders(() => useEpisode('ds-1', 0))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data).toEqual({
      meta: { index: 0, length: 100, taskIndex: 0, hasAnnotations: false },
      videoUrls: { front: 'http://example/front.mp4' },
      cameras: ['front'],
      trajectoryData: [],
    })
    expect(mockFetch).toHaveBeenCalledWith('/api/datasets/ds-1/episodes/0', expect.any(Object))
  })
})

describe('useCapabilities', () => {
  it('does not fetch when datasetId is undefined', () => {
    renderHookWithProviders(() => useCapabilities(undefined))
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('fetches capabilities for the dataset', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ has_videos: true, has_trajectory: true, camera_keys: ['front'] }),
    )

    const { result } = renderHookWithProviders(() => useCapabilities('ds-1'))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data).toEqual({
      hasVideos: true,
      hasTrajectory: true,
      cameraKeys: ['front'],
    })
  })
})

describe('useCacheStats', () => {
  it('does not fetch when disabled', () => {
    renderHookWithProviders(() => useCacheStats(false))
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('fetches cache stats and transforms the response when enabled', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        capacity: 100,
        size: 25,
        hits: 80,
        misses: 20,
        hit_rate: 0.8,
        total_bytes: 1024,
        max_memory_bytes: 4096,
      }),
    )

    const { result } = renderHookWithProviders(() => useCacheStats(true))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data).toEqual({
      capacity: 100,
      size: 25,
      hits: 80,
      misses: 20,
      hitRate: 0.8,
      totalBytes: 1024,
      maxMemoryBytes: 4096,
    })
    expect(mockFetch).toHaveBeenCalledWith('/api/datasets/cache/stats', expect.any(Object))
  })
})
