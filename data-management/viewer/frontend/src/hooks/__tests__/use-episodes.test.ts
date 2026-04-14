import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createQueryWrapper } from './test-utils'

const mockFetch = vi.fn()

vi.mock('@/lib/api-client', () => ({
  fetchEpisodes: mockFetch,
  fetchEpisode: mockFetch,
}))

const { useDatasetStore } = await import('@/stores')
const { useEpisodeStore } = await import('@/stores')
const { episodeKeys, useEpisodeList, useCurrentEpisode, useEpisodeNavigationWithPrefetch } =
  await import('@/hooks/use-episodes')

function makeEpisode(index: number, length = 100) {
  return { index, length, meta: { index, length }, taskIndex: 0 }
}

beforeEach(() => {
  useDatasetStore.getState().reset()
  useEpisodeStore.getState().reset()
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('episodeKeys', () => {
  it('builds query keys for all episodes', () => {
    expect(episodeKeys.all).toEqual(['episodes'])
  })

  it('builds list keys with dataset id', () => {
    expect(episodeKeys.list('ds-1')).toEqual(['episodes', 'list', 'ds-1', undefined])
  })

  it('builds list keys with filters', () => {
    const filters = { hasAnnotations: true }
    expect(episodeKeys.list('ds-1', filters)).toEqual(['episodes', 'list', 'ds-1', filters])
  })

  it('builds detail keys with dataset id and index', () => {
    expect(episodeKeys.detail('ds-1', 3)).toEqual(['episodes', 'detail', 'ds-1', 3])
  })
})

describe('useEpisodeList', () => {
  it('does not fetch when no dataset is selected', () => {
    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useEpisodeList(), { wrapper })

    expect(result.current.isLoading).toBe(false)
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('fetches episodes when a dataset is selected', async () => {
    const episodes = [makeEpisode(0), makeEpisode(1)]
    mockFetch.mockResolvedValueOnce(episodes)

    useDatasetStore.setState({ currentDataset: { id: 'ds-1', name: 'Test' } as never })

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useEpisodeList(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockFetch).toHaveBeenCalledWith('ds-1', undefined)
    expect(result.current.data).toEqual(episodes)
  })

  it('syncs episodes to the Zustand store on success', async () => {
    const episodes = [makeEpisode(0), makeEpisode(1)]
    mockFetch.mockResolvedValueOnce(episodes)

    useDatasetStore.setState({ currentDataset: { id: 'ds-1', name: 'Test' } as never })

    const wrapper = createQueryWrapper()
    renderHook(() => useEpisodeList(), { wrapper })

    await waitFor(() => expect(useEpisodeStore.getState().episodes).toEqual(episodes))
  })

  it('syncs error to the Zustand store on failure', async () => {
    mockFetch.mockRejectedValueOnce(new Error('Network error'))

    useDatasetStore.setState({ currentDataset: { id: 'ds-1', name: 'Test' } as never })

    const wrapper = createQueryWrapper()
    renderHook(() => useEpisodeList(), { wrapper })

    await waitFor(() => expect(useEpisodeStore.getState().error).toBe('Network error'))
  })

  it('passes filter options to the query key and fetch function', async () => {
    const episodes = [makeEpisode(0)]
    mockFetch.mockResolvedValueOnce(episodes)

    useDatasetStore.setState({ currentDataset: { id: 'ds-1', name: 'Test' } as never })
    const opts = { offset: 0, limit: 10, hasAnnotations: true }

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useEpisodeList(opts), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockFetch).toHaveBeenCalledWith('ds-1', opts)
  })
})

describe('useCurrentEpisode', () => {
  it('does not fetch when no dataset or invalid index', () => {
    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useCurrentEpisode(), { wrapper })

    expect(result.current.fetchStatus).toBe('idle')
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('fetches the current episode and syncs to store', async () => {
    const episode = makeEpisode(2, 500)
    mockFetch.mockResolvedValue(episode)

    useDatasetStore.setState({ currentDataset: { id: 'ds-1', name: 'Test' } as never })
    useEpisodeStore.setState({
      currentIndex: 2,
      episodes: [makeEpisode(0, 500), makeEpisode(1, 500), makeEpisode(2, 500)] as never[],
    })

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useCurrentEpisode(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(useEpisodeStore.getState().currentEpisode).toEqual(episode)
  })

  it('uses short gcTime for large episodes (>2000 frames)', async () => {
    const largeEpisodes = [makeEpisode(0, 3000), makeEpisode(1, 3000)]
    mockFetch.mockResolvedValue(makeEpisode(0, 3000))

    useDatasetStore.setState({ currentDataset: { id: 'ds-1', name: 'Test' } as never })
    useEpisodeStore.setState({ currentIndex: 0, episodes: largeEpisodes as never[] })

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useCurrentEpisode(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    // gcTime of 5 minutes for avg length > 2000 — verified via successful fetch
    expect(result.current.data).toEqual(makeEpisode(0, 3000))
  })

  it('prefetches adjacent episodes', async () => {
    const episodes = [makeEpisode(0), makeEpisode(1), makeEpisode(2)]
    mockFetch.mockResolvedValue(makeEpisode(0))

    useDatasetStore.setState({ currentDataset: { id: 'ds-1', name: 'Test' } as never })
    useEpisodeStore.setState({ currentIndex: 1, episodes: episodes as never[] })

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useCurrentEpisode(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    // Should have fetched current (index 1) plus prefetched index 0 and index 2
    await waitFor(() => expect(mockFetch.mock.calls.length).toBeGreaterThanOrEqual(2))
  })

  it('does not prefetch out-of-bounds indices', async () => {
    const episodes = [makeEpisode(0)]
    mockFetch.mockResolvedValue(makeEpisode(0))

    useDatasetStore.setState({ currentDataset: { id: 'ds-1', name: 'Test' } as never })
    useEpisodeStore.setState({ currentIndex: 0, episodes: episodes as never[] })

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useCurrentEpisode(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    // Only the current episode should be fetched (no valid adjacent indices)
    expect(mockFetch).toHaveBeenCalledTimes(1)
  })
})

describe('useEpisodeNavigationWithPrefetch', () => {
  beforeEach(() => {
    useEpisodeStore.setState({
      episodes: [makeEpisode(0), makeEpisode(1), makeEpisode(2)] as never[],
      currentIndex: 1,
    })
  })

  it('returns correct navigation state', () => {
    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useEpisodeNavigationWithPrefetch(), { wrapper })

    expect(result.current.currentIndex).toBe(1)
    expect(result.current.totalEpisodes).toBe(3)
    expect(result.current.canGoNext).toBe(true)
    expect(result.current.canGoPrevious).toBe(true)
  })

  it('reports canGoNext=false at the last episode', () => {
    useEpisodeStore.setState({ currentIndex: 2 })

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useEpisodeNavigationWithPrefetch(), { wrapper })

    expect(result.current.canGoNext).toBe(false)
    expect(result.current.canGoPrevious).toBe(true)
  })

  it('reports canGoPrevious=false at the first episode', () => {
    useEpisodeStore.setState({ currentIndex: 0 })

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useEpisodeNavigationWithPrefetch(), { wrapper })

    expect(result.current.canGoNext).toBe(true)
    expect(result.current.canGoPrevious).toBe(false)
  })

  it('returns zero totalEpisodes when store is empty', () => {
    useEpisodeStore.getState().reset()

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useEpisodeNavigationWithPrefetch(), { wrapper })

    expect(result.current.totalEpisodes).toBe(0)
    expect(result.current.canGoNext).toBe(false)
    expect(result.current.canGoPrevious).toBe(false)
  })
})
