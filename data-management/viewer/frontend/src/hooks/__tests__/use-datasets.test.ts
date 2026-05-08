import { waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useCacheStats,
  useCapabilities,
  useDataset,
  useDatasets,
  useEpisode,
  useEpisodes,
} from '@/hooks/use-datasets'
import { useDatasetStore } from '@/stores'
import { renderHookWithQuery } from '@/test/render-hook-with-query'

vi.mock('@/lib/api-client', () => ({
  fetchDatasets: vi.fn(),
  fetchDataset: vi.fn(),
  fetchEpisodes: vi.fn(),
  fetchEpisode: vi.fn(),
  fetchCapabilities: vi.fn(),
  fetchCacheStats: vi.fn(),
}))

import {
  fetchCacheStats,
  fetchCapabilities,
  fetchDataset,
  fetchDatasets,
  fetchEpisode,
  fetchEpisodes,
} from '@/lib/api-client'

const mockedDatasets = vi.mocked(fetchDatasets)
const mockedDataset = vi.mocked(fetchDataset)
const mockedEpisodes = vi.mocked(fetchEpisodes)
const mockedEpisode = vi.mocked(fetchEpisode)
const mockedCapabilities = vi.mocked(fetchCapabilities)
const mockedCacheStats = vi.mocked(fetchCacheStats)

beforeEach(() => {
  mockedDatasets.mockReset()
  mockedDataset.mockReset()
  mockedEpisodes.mockReset()
  mockedEpisode.mockReset()
  mockedCapabilities.mockReset()
  mockedCacheStats.mockReset()
  useDatasetStore.getState().reset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useDatasets', () => {
  it('fetches datasets and syncs them to the dataset store', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const datasets = [{ id: 'ds-1', name: 'One' } as any, { id: 'ds-2', name: 'Two' } as any]
    mockedDatasets.mockResolvedValueOnce(datasets)

    const { result } = renderHookWithQuery(() => useDatasets())

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(datasets)
    expect(useDatasetStore.getState().datasets).toEqual(datasets)
  })

  it('writes error message into the dataset store when query fails', async () => {
    mockedDatasets.mockRejectedValueOnce(new Error('boom'))

    const { result } = renderHookWithQuery(() => useDatasets())

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(useDatasetStore.getState().error).toBe('boom')
  })
})

describe('useDataset', () => {
  it('fetches a single dataset by id', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const ds = { id: 'ds-1', name: 'One' } as any
    mockedDataset.mockResolvedValueOnce(ds)

    const { result } = renderHookWithQuery(() => useDataset('ds-1'))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockedDataset).toHaveBeenCalledWith('ds-1')
    expect(result.current.data).toEqual(ds)
  })

  it('is disabled when datasetId is undefined', () => {
    const { result } = renderHookWithQuery(() => useDataset(undefined))
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockedDataset).not.toHaveBeenCalled()
  })
})

describe('useEpisodes', () => {
  it('fetches episodes and forwards options to the API client', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const episodes = [{ episode_index: 0 } as any]
    mockedEpisodes.mockResolvedValueOnce(episodes)

    const { result } = renderHookWithQuery(() =>
      useEpisodes('ds-1', { offset: 10, limit: 20, hasAnnotations: true }),
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockedEpisodes).toHaveBeenCalledWith('ds-1', {
      offset: 10,
      limit: 20,
      hasAnnotations: true,
    })
  })

  it('is disabled when datasetId is undefined', () => {
    const { result } = renderHookWithQuery(() => useEpisodes(undefined))
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockedEpisodes).not.toHaveBeenCalled()
  })
})

describe('useEpisode', () => {
  it('fetches a specific episode by index', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const episode = { episode_index: 7 } as any
    mockedEpisode.mockResolvedValueOnce(episode)

    const { result } = renderHookWithQuery(() => useEpisode('ds-1', 7))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockedEpisode).toHaveBeenCalledWith('ds-1', 7)
  })

  it('is disabled when episodeIndex is undefined', () => {
    const { result } = renderHookWithQuery(() => useEpisode('ds-1', undefined))
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockedEpisode).not.toHaveBeenCalled()
  })

  it('is disabled when episodeIndex is negative', () => {
    const { result } = renderHookWithQuery(() => useEpisode('ds-1', -1))
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockedEpisode).not.toHaveBeenCalled()
  })
})

describe('useCapabilities', () => {
  it('fetches capabilities for a dataset', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const caps = { supports_video: true } as any
    mockedCapabilities.mockResolvedValueOnce(caps)

    const { result } = renderHookWithQuery(() => useCapabilities('ds-1'))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockedCapabilities).toHaveBeenCalledWith('ds-1')
  })

  it('is disabled when datasetId is undefined', () => {
    const { result } = renderHookWithQuery(() => useCapabilities(undefined))
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockedCapabilities).not.toHaveBeenCalled()
  })
})

describe('useCacheStats', () => {
  it('fetches cache stats when enabled', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const stats = { hits: 1, misses: 2 } as any
    mockedCacheStats.mockResolvedValueOnce(stats)

    const { result } = renderHookWithQuery(() => useCacheStats(true))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockedCacheStats).toHaveBeenCalled()
  })

  it('does not fetch when disabled', () => {
    const { result } = renderHookWithQuery(() => useCacheStats(false))
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockedCacheStats).not.toHaveBeenCalled()
  })
})
