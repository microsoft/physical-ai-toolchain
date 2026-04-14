import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createQueryWrapper } from './test-utils'

// ============================================================================
// Mocks
// ============================================================================

const mockApiClient = vi.hoisted(() => ({
  fetchDatasets: vi.fn().mockResolvedValue([]),
  fetchDataset: vi.fn().mockResolvedValue(null),
  fetchEpisodes: vi.fn().mockResolvedValue([]),
  fetchEpisode: vi.fn().mockResolvedValue(null),
  fetchCapabilities: vi.fn().mockResolvedValue({}),
  fetchCacheStats: vi.fn().mockResolvedValue({}),
}))

const mockStore = vi.hoisted(() => ({
  setDatasets: vi.fn(),
  setLoading: vi.fn(),
  setError: vi.fn(),
}))

vi.mock('@/lib/api-client', () => mockApiClient)
vi.mock('@/stores', () => ({
  useDatasetStore: (selector: (state: typeof mockStore) => unknown) => selector(mockStore),
}))

// ============================================================================
// Fixtures
// ============================================================================

const testDatasets = [
  { id: 'ds-1', name: 'Dataset 1', episodeCount: 10 },
  { id: 'ds-2', name: 'Dataset 2', episodeCount: 5 },
]

const testDataset = { id: 'ds-1', name: 'Dataset 1', episodeCount: 10 }

const testEpisodes = [
  { index: 0, datasetId: 'ds-1' },
  { index: 1, datasetId: 'ds-1' },
]

const testEpisode = { index: 0, datasetId: 'ds-1', frames: [] }

const testCapabilities = { hasJointData: true, hasCameraData: true }

const testCacheStats = { hitRate: 0.85, totalRequests: 100, cacheSize: 50 }

// ============================================================================
// Tests
// ============================================================================

describe('datasetKeys', () => {
  it('generates hierarchical query keys', async () => {
    const { datasetKeys } = await import('@/hooks/use-datasets')

    expect(datasetKeys.all).toEqual(['datasets'])
    expect(datasetKeys.lists()).toEqual(['datasets', 'list'])
    expect(datasetKeys.list()).toEqual(['datasets', 'list'])
    expect(datasetKeys.details()).toEqual(['datasets', 'detail'])
    expect(datasetKeys.detail('ds-1')).toEqual(['datasets', 'detail', 'ds-1'])
    expect(datasetKeys.episodes('ds-1')).toEqual(['datasets', 'detail', 'ds-1', 'episodes'])
    expect(datasetKeys.episode('ds-1', 0)).toEqual(['datasets', 'detail', 'ds-1', 'episodes', 0])
  })
})

describe('capabilityKeys', () => {
  it('generates capability query keys', async () => {
    const { capabilityKeys } = await import('@/hooks/use-datasets')

    expect(capabilityKeys.all).toEqual(['capabilities'])
    expect(capabilityKeys.detail('ds-1')).toEqual(['capabilities', 'ds-1'])
  })
})

describe('cacheStatsKeys', () => {
  it('generates cache stats query keys', async () => {
    const { cacheStatsKeys } = await import('@/hooks/use-datasets')

    expect(cacheStatsKeys.all).toEqual(['cache-stats'])
  })
})

// ============================================================================
// useDatasets
// ============================================================================

describe('useDatasets', () => {
  let wrapper: ReturnType<typeof createQueryWrapper>

  beforeEach(() => {
    wrapper = createQueryWrapper()
    vi.clearAllMocks()
    mockApiClient.fetchDatasets.mockResolvedValue(testDatasets)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetches datasets and returns query result', async () => {
    const { useDatasets } = await import('@/hooks/use-datasets')
    const { result } = renderHook(() => useDatasets(), { wrapper })

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(result.current.data).toEqual(testDatasets)
    expect(mockApiClient.fetchDatasets).toHaveBeenCalled()
  })

  it('syncs loading state to store', async () => {
    const { useDatasets } = await import('@/hooks/use-datasets')
    renderHook(() => useDatasets(), { wrapper })

    await waitFor(() => {
      expect(mockStore.setLoading).toHaveBeenCalled()
    })
  })

  it('syncs datasets to store on success', async () => {
    const { useDatasets } = await import('@/hooks/use-datasets')
    renderHook(() => useDatasets(), { wrapper })

    await waitFor(() => {
      expect(mockStore.setDatasets).toHaveBeenCalledWith(testDatasets)
    })
  })

  it('syncs error to store on failure', async () => {
    mockApiClient.fetchDatasets.mockRejectedValue(new Error('Network error'))
    const { useDatasets } = await import('@/hooks/use-datasets')
    renderHook(() => useDatasets(), { wrapper })

    await waitFor(() => {
      expect(mockStore.setError).toHaveBeenCalledWith('Network error')
    })
  })
})

// ============================================================================
// useDataset
// ============================================================================

describe('useDataset', () => {
  let wrapper: ReturnType<typeof createQueryWrapper>

  beforeEach(() => {
    wrapper = createQueryWrapper()
    vi.clearAllMocks()
    mockApiClient.fetchDataset.mockResolvedValue(testDataset)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetches a single dataset by ID', async () => {
    const { useDataset } = await import('@/hooks/use-datasets')
    const { result } = renderHook(() => useDataset('ds-1'), { wrapper })

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(result.current.data).toEqual(testDataset)
    expect(mockApiClient.fetchDataset).toHaveBeenCalledWith('ds-1')
  })

  it('is disabled when datasetId is undefined', async () => {
    const { useDataset } = await import('@/hooks/use-datasets')
    const { result } = renderHook(() => useDataset(undefined), { wrapper })

    expect(result.current.fetchStatus).toBe('idle')
    expect(mockApiClient.fetchDataset).not.toHaveBeenCalled()
  })
})

// ============================================================================
// useEpisodes
// ============================================================================

describe('useEpisodes', () => {
  let wrapper: ReturnType<typeof createQueryWrapper>

  beforeEach(() => {
    wrapper = createQueryWrapper()
    vi.clearAllMocks()
    mockApiClient.fetchEpisodes.mockResolvedValue(testEpisodes)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetches episodes for a dataset', async () => {
    const { useEpisodes } = await import('@/hooks/use-datasets')
    const { result } = renderHook(() => useEpisodes('ds-1'), { wrapper })

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(result.current.data).toEqual(testEpisodes)
    expect(mockApiClient.fetchEpisodes).toHaveBeenCalledWith('ds-1', undefined)
  })

  it('passes options to fetchEpisodes', async () => {
    const { useEpisodes } = await import('@/hooks/use-datasets')
    const options = { offset: 10, limit: 50, hasAnnotations: true }
    renderHook(() => useEpisodes('ds-1', options), { wrapper })

    await waitFor(() => {
      expect(mockApiClient.fetchEpisodes).toHaveBeenCalledWith('ds-1', options)
    })
  })

  it('is disabled when datasetId is undefined', async () => {
    const { useEpisodes } = await import('@/hooks/use-datasets')
    const { result } = renderHook(() => useEpisodes(undefined), { wrapper })

    expect(result.current.fetchStatus).toBe('idle')
    expect(mockApiClient.fetchEpisodes).not.toHaveBeenCalled()
  })
})

// ============================================================================
// useEpisode
// ============================================================================

describe('useEpisode', () => {
  let wrapper: ReturnType<typeof createQueryWrapper>

  beforeEach(() => {
    wrapper = createQueryWrapper()
    vi.clearAllMocks()
    mockApiClient.fetchEpisode.mockResolvedValue(testEpisode)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetches a specific episode', async () => {
    const { useEpisode } = await import('@/hooks/use-datasets')
    const { result } = renderHook(() => useEpisode('ds-1', 0), { wrapper })

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(result.current.data).toEqual(testEpisode)
    expect(mockApiClient.fetchEpisode).toHaveBeenCalledWith('ds-1', 0)
  })

  it('is disabled when datasetId is undefined', async () => {
    const { useEpisode } = await import('@/hooks/use-datasets')
    const { result } = renderHook(() => useEpisode(undefined, 0), { wrapper })

    expect(result.current.fetchStatus).toBe('idle')
  })

  it('is disabled when episodeIndex is undefined', async () => {
    const { useEpisode } = await import('@/hooks/use-datasets')
    const { result } = renderHook(() => useEpisode('ds-1', undefined), { wrapper })

    expect(result.current.fetchStatus).toBe('idle')
  })

  it('is disabled when episodeIndex is negative', async () => {
    const { useEpisode } = await import('@/hooks/use-datasets')
    const { result } = renderHook(() => useEpisode('ds-1', -1), { wrapper })

    expect(result.current.fetchStatus).toBe('idle')
  })
})

// ============================================================================
// useCapabilities
// ============================================================================

describe('useCapabilities', () => {
  let wrapper: ReturnType<typeof createQueryWrapper>

  beforeEach(() => {
    wrapper = createQueryWrapper()
    vi.clearAllMocks()
    mockApiClient.fetchCapabilities.mockResolvedValue(testCapabilities)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetches capabilities for a dataset', async () => {
    const { useCapabilities } = await import('@/hooks/use-datasets')
    const { result } = renderHook(() => useCapabilities('ds-1'), { wrapper })

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(result.current.data).toEqual(testCapabilities)
    expect(mockApiClient.fetchCapabilities).toHaveBeenCalledWith('ds-1')
  })

  it('is disabled when datasetId is undefined', async () => {
    const { useCapabilities } = await import('@/hooks/use-datasets')
    const { result } = renderHook(() => useCapabilities(undefined), { wrapper })

    expect(result.current.fetchStatus).toBe('idle')
  })
})

// ============================================================================
// useCacheStats
// ============================================================================

describe('useCacheStats', () => {
  let wrapper: ReturnType<typeof createQueryWrapper>

  beforeEach(() => {
    wrapper = createQueryWrapper()
    vi.clearAllMocks()
    mockApiClient.fetchCacheStats.mockResolvedValue(testCacheStats)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetches cache stats when enabled', async () => {
    const { useCacheStats } = await import('@/hooks/use-datasets')
    const { result } = renderHook(() => useCacheStats(true), { wrapper })

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(result.current.data).toEqual(testCacheStats)
    expect(mockApiClient.fetchCacheStats).toHaveBeenCalled()
  })

  it('is disabled when enabled is false', async () => {
    const { useCacheStats } = await import('@/hooks/use-datasets')
    const { result } = renderHook(() => useCacheStats(false), { wrapper })

    expect(result.current.fetchStatus).toBe('idle')
    expect(mockApiClient.fetchCacheStats).not.toHaveBeenCalled()
  })
})
