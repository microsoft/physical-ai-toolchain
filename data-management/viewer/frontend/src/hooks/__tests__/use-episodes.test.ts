import { waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useCurrentEpisode,
  useEpisodeList,
  useEpisodeNavigationWithPrefetch,
} from '@/hooks/use-episodes'
import { useDatasetStore, useEpisodeStore } from '@/stores'
import { createTestQueryClient, renderHookWithQuery } from '@/test/render-hook-with-query'

vi.mock('@/lib/api-client', () => ({
  fetchEpisode: vi.fn(),
  fetchEpisodes: vi.fn(),
}))

import { fetchEpisode, fetchEpisodes } from '@/lib/api-client'

const mockedFetchEpisode = vi.mocked(fetchEpisode)
const mockedFetchEpisodes = vi.mocked(fetchEpisodes)

beforeEach(() => {
  mockedFetchEpisode.mockReset()
  mockedFetchEpisodes.mockReset()
  useDatasetStore.getState().reset()
  useEpisodeStore.getState().reset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

function selectDataset(id: string): void {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  useDatasetStore.getState().setDatasets([{ id, name: id } as any])
  useDatasetStore.getState().selectDataset(id)
}

describe('useEpisodeList', () => {
  it('is disabled when no dataset is selected', () => {
    const { result } = renderHookWithQuery(() => useEpisodeList())
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockedFetchEpisodes).not.toHaveBeenCalled()
  })

  it('fetches episodes and syncs them into the episode store', async () => {
    selectDataset('ds-1')
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const episodes = [{ meta: { index: 0, length: 100 } } as any]
    mockedFetchEpisodes.mockResolvedValueOnce(episodes)

    const { result } = renderHookWithQuery(() => useEpisodeList({ offset: 0, limit: 10 }))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockedFetchEpisodes).toHaveBeenCalledWith('ds-1', { offset: 0, limit: 10 })
    expect(useEpisodeStore.getState().episodes).toEqual(episodes)
  })

  it('writes the error message into the episode store on failure', async () => {
    selectDataset('ds-1')
    mockedFetchEpisodes.mockRejectedValueOnce(new Error('list failed'))

    const { result } = renderHookWithQuery(() => useEpisodeList())

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(useEpisodeStore.getState().error).toBe('list failed')
  })
})

describe('useCurrentEpisode', () => {
  it('is disabled when no dataset is selected', () => {
    const { result } = renderHookWithQuery(() => useCurrentEpisode())
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockedFetchEpisode).not.toHaveBeenCalled()
  })

  it('is disabled when current index is negative', () => {
    selectDataset('ds-1')
    const { result } = renderHookWithQuery(() => useCurrentEpisode())
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockedFetchEpisode).not.toHaveBeenCalled()
  })

  it('fetches the current episode and syncs it into the store', async () => {
    selectDataset('ds-1')
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const episode = { meta: { index: 1, length: 50 } } as any
    // Seed three episodes so prefetch logic has neighbors to act on
    useEpisodeStore.getState().setEpisodes([
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { meta: { index: 0, length: 50 }, length: 50 } as any,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { meta: { index: 1, length: 50 }, length: 50 } as any,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { meta: { index: 2, length: 50 }, length: 50 } as any,
    ])
    useEpisodeStore.getState().setCurrentEpisode(episode)
    mockedFetchEpisode.mockResolvedValue(episode)

    const { result } = renderHookWithQuery(() => useCurrentEpisode())

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockedFetchEpisode).toHaveBeenCalledWith('ds-1', 1)
    expect(useEpisodeStore.getState().currentEpisode).toEqual(episode)
  })

  it('prefetches adjacent episodes when current episode loads', async () => {
    selectDataset('ds-1')
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const episode = { meta: { index: 1, length: 50 } } as any
    useEpisodeStore.getState().setEpisodes([
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { meta: { index: 0, length: 50 }, length: 50 } as any,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { meta: { index: 1, length: 50 }, length: 50 } as any,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { meta: { index: 2, length: 50 }, length: 50 } as any,
    ])
    useEpisodeStore.getState().setCurrentEpisode(episode)
    mockedFetchEpisode.mockResolvedValue(episode)

    const queryClient = createTestQueryClient()
    const prefetchSpy = vi.spyOn(queryClient, 'prefetchQuery')
    const rendered = renderHookWithQuery(() => useCurrentEpisode(), { queryClient })

    await waitFor(() => expect(rendered.result.current.isSuccess).toBe(true))
    await waitFor(() => {
      expect(prefetchSpy).toHaveBeenCalled()
    })

    // At least one prefetch call for indices 0 and 2 should have been made.
    const prefetchedIndices = prefetchSpy.mock.calls
      .map((call) => call[0]?.queryKey)
      .filter((key): key is readonly unknown[] => Array.isArray(key))
      .map((key) => key[key.length - 1])
    expect(prefetchedIndices).toEqual(expect.arrayContaining([0, 2]))
  })

  it('writes the error message into the episode store on failure', async () => {
    selectDataset('ds-1')
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    useEpisodeStore.getState().setCurrentEpisode({ meta: { index: 0, length: 1 } } as any)
    mockedFetchEpisode.mockRejectedValueOnce(new Error('episode failed'))

    const { result } = renderHookWithQuery(() => useCurrentEpisode())

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(useEpisodeStore.getState().error).toBe('episode failed')
  })
})

describe('useEpisodeNavigationWithPrefetch', () => {
  it('exposes navigation flags reflecting the current store state', () => {
    useEpisodeStore.getState().setEpisodes([
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { meta: { index: 0, length: 1 }, length: 1 } as any,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { meta: { index: 1, length: 1 }, length: 1 } as any,
    ])
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    useEpisodeStore.getState().setCurrentEpisode({ meta: { index: 0, length: 1 } } as any)

    const { result } = renderHookWithQuery(() => useEpisodeNavigationWithPrefetch())

    expect(result.current.canGoPrevious).toBe(false)
    expect(result.current.canGoNext).toBe(true)
    expect(result.current.currentIndex).toBe(0)
    expect(result.current.totalEpisodes).toBe(2)
  })

  it('delegates navigation calls to the episode store', () => {
    useEpisodeStore.getState().setEpisodes([
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { meta: { index: 0, length: 1 }, length: 1 } as any,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { meta: { index: 1, length: 1 }, length: 1 } as any,
    ])
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    useEpisodeStore.getState().setCurrentEpisode({ meta: { index: 0, length: 1 } } as any)

    const { result } = renderHookWithQuery(() => useEpisodeNavigationWithPrefetch())

    result.current.goNext()
    expect(useEpisodeStore.getState().currentIndex).toBe(1)

    result.current.goPrevious()
    expect(useEpisodeStore.getState().currentIndex).toBe(0)

    result.current.goToEpisode(1)
    expect(useEpisodeStore.getState().currentIndex).toBe(1)
  })
})
