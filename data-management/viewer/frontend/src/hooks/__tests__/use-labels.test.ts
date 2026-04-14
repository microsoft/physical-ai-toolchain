import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createQueryWrapper } from './test-utils'

// ============================================================================
// Mocks
// ============================================================================

const mockFetch = vi.fn()

const mockDatasetStore = vi.hoisted(() => ({
  currentDataset: { id: 'ds-1', name: 'Dataset 1' } as { id: string; name: string } | null,
}))

const mockLabelStore = vi.hoisted(() => ({
  setAvailableLabels: vi.fn(),
  setAllEpisodeLabels: vi.fn(),
  setLoaded: vi.fn(),
  commitEpisodeLabels: vi.fn(),
  removeLabelOption: vi.fn(),
  episodeLabels: {} as Record<number, string[]>,
  toggleLabel: vi.fn(),
}))

vi.mock('@/stores', () => ({
  useDatasetStore: (selector: (state: typeof mockDatasetStore) => unknown) =>
    selector(mockDatasetStore),
}))

vi.mock('@/stores/label-store', () => ({
  useLabelStore: (selector: (state: typeof mockLabelStore) => unknown) => selector(mockLabelStore),
}))

// ============================================================================
// Helpers
// ============================================================================

function jsonResponse(data: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    json: () => Promise.resolve(data),
  }
}

// ============================================================================
// Fixtures
// ============================================================================

const testLabelsResponse = {
  dataset_id: 'ds-1',
  available_labels: ['GOOD', 'BAD', 'REVIEW'],
  episodes: { '0': ['GOOD'], '1': ['BAD', 'REVIEW'] },
}

// ============================================================================
// Tests
// ============================================================================

describe('labelKeys', () => {
  it('generates hierarchical label query keys', async () => {
    const { labelKeys } = await import('@/hooks/use-labels')

    expect(labelKeys.all).toEqual(['labels'])
    expect(labelKeys.dataset('ds-1')).toEqual(['labels', 'ds-1'])
    expect(labelKeys.options('ds-1')).toEqual(['labels', 'ds-1', 'options'])
    expect(labelKeys.episode('ds-1', 0)).toEqual(['labels', 'ds-1', 'episode', 0])
  })
})

// ============================================================================
// useDatasetLabels
// ============================================================================

describe('useDatasetLabels', () => {
  const wrapper = createQueryWrapper()

  beforeEach(() => {
    mockFetch.mockReset()
    vi.stubGlobal('fetch', mockFetch)
    mockDatasetStore.currentDataset = { id: 'ds-1', name: 'Dataset 1' }
    mockLabelStore.setAvailableLabels.mockClear()
    mockLabelStore.setAllEpisodeLabels.mockClear()
    mockLabelStore.setLoaded.mockClear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetches and syncs labels to store', async () => {
    mockFetch.mockResolvedValue(jsonResponse(testLabelsResponse))
    const { useDatasetLabels } = await import('@/hooks/use-labels')
    renderHook(() => useDatasetLabels(), { wrapper })

    await waitFor(() => {
      expect(mockLabelStore.setAvailableLabels).toHaveBeenCalledWith(['GOOD', 'BAD', 'REVIEW'])
    })

    expect(mockLabelStore.setAllEpisodeLabels).toHaveBeenCalledWith(testLabelsResponse.episodes)
    expect(mockLabelStore.setLoaded).toHaveBeenCalledWith(true)
  })

  it('is disabled when no current dataset', async () => {
    mockDatasetStore.currentDataset = null
    const { useDatasetLabels } = await import('@/hooks/use-labels')
    const { result } = renderHook(() => useDatasetLabels(), { wrapper })

    expect(result.current.fetchStatus).toBe('idle')
    expect(mockFetch).not.toHaveBeenCalled()
  })
})

// ============================================================================
// useSaveEpisodeLabels
// ============================================================================

describe('useSaveEpisodeLabels', () => {
  const wrapper = createQueryWrapper()

  beforeEach(() => {
    mockFetch.mockReset()
    vi.stubGlobal('fetch', mockFetch)
    mockDatasetStore.currentDataset = { id: 'ds-1', name: 'Dataset 1' }
    mockLabelStore.commitEpisodeLabels.mockClear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('saves labels via PUT and commits to store', async () => {
    const response = { episode_index: 0, labels: ['GOOD'] }
    mockFetch.mockResolvedValue(jsonResponse(response))
    const { useSaveEpisodeLabels } = await import('@/hooks/use-labels')
    const { result } = renderHook(() => useSaveEpisodeLabels(), { wrapper })

    await act(async () => {
      result.current.mutate({ episodeIdx: 0, labels: ['GOOD'] })
    })

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/datasets/ds-1/episodes/0/labels',
      expect.objectContaining({ method: 'PUT' }),
    )
    expect(mockLabelStore.commitEpisodeLabels).toHaveBeenCalledWith(0, ['GOOD'])
  })

  it('throws when no dataset selected', async () => {
    mockDatasetStore.currentDataset = null
    const { useSaveEpisodeLabels } = await import('@/hooks/use-labels')
    const { result } = renderHook(() => useSaveEpisodeLabels(), { wrapper })

    await act(async () => {
      result.current.mutate({ episodeIdx: 0, labels: ['GOOD'] })
    })

    await waitFor(() => {
      expect(result.current.isError).toBe(true)
    })

    expect(result.current.error?.message).toBe('No dataset selected')
  })
})

// ============================================================================
// useAddLabelOption
// ============================================================================

describe('useAddLabelOption', () => {
  const wrapper = createQueryWrapper()

  beforeEach(() => {
    mockFetch.mockReset()
    vi.stubGlobal('fetch', mockFetch)
    mockDatasetStore.currentDataset = { id: 'ds-1', name: 'Dataset 1' }
    mockLabelStore.setAvailableLabels.mockClear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('adds a label via POST and updates store', async () => {
    const updatedLabels = ['GOOD', 'BAD', 'REVIEW', 'NEW']
    mockFetch.mockResolvedValue(jsonResponse(updatedLabels))
    const { useAddLabelOption } = await import('@/hooks/use-labels')
    const { result } = renderHook(() => useAddLabelOption(), { wrapper })

    await act(async () => {
      result.current.mutate('NEW')
    })

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/datasets/ds-1/labels/options',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(mockLabelStore.setAvailableLabels).toHaveBeenCalledWith(updatedLabels)
  })

  it('throws when no dataset selected', async () => {
    mockDatasetStore.currentDataset = null
    const { useAddLabelOption } = await import('@/hooks/use-labels')
    const { result } = renderHook(() => useAddLabelOption(), { wrapper })

    await act(async () => {
      result.current.mutate('NEW')
    })

    await waitFor(() => {
      expect(result.current.isError).toBe(true)
    })
  })
})

// ============================================================================
// useRemoveLabelOption
// ============================================================================

describe('useRemoveLabelOption', () => {
  const wrapper = createQueryWrapper()

  beforeEach(() => {
    mockFetch.mockReset()
    vi.stubGlobal('fetch', mockFetch)
    mockDatasetStore.currentDataset = { id: 'ds-1', name: 'Dataset 1' }
    mockLabelStore.removeLabelOption.mockClear()
    mockLabelStore.setAvailableLabels.mockClear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('removes a label via DELETE with encoded URI and updates store', async () => {
    const remaining = ['GOOD', 'REVIEW']
    mockFetch.mockResolvedValue(jsonResponse(remaining))
    const { useRemoveLabelOption } = await import('@/hooks/use-labels')
    const { result } = renderHook(() => useRemoveLabelOption(), { wrapper })

    await act(async () => {
      result.current.mutate('bad')
    })

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/datasets/ds-1/labels/options/BAD',
      expect.objectContaining({ method: 'DELETE' }),
    )
    expect(mockLabelStore.removeLabelOption).toHaveBeenCalledWith('bad')
    expect(mockLabelStore.setAvailableLabels).toHaveBeenCalledWith(remaining)
  })
})

// ============================================================================
// useCurrentEpisodeLabels
// ============================================================================

describe('useCurrentEpisodeLabels', () => {
  beforeEach(() => {
    mockLabelStore.toggleLabel.mockClear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('returns labels for the given episode index', async () => {
    mockLabelStore.episodeLabels = { 0: ['GOOD', 'REVIEW'], 1: ['BAD'] }
    const { useCurrentEpisodeLabels } = await import('@/hooks/use-labels')
    const { result } = renderHook(() => useCurrentEpisodeLabels(0))

    expect(result.current.currentLabels).toEqual(['GOOD', 'REVIEW'])
  })

  it('returns empty array for unknown episode', async () => {
    mockLabelStore.episodeLabels = {}
    const { useCurrentEpisodeLabels } = await import('@/hooks/use-labels')
    const { result } = renderHook(() => useCurrentEpisodeLabels(99))

    expect(result.current.currentLabels).toEqual([])
  })

  it('toggle calls store toggleLabel', async () => {
    mockLabelStore.episodeLabels = { 0: ['GOOD'] }
    const { useCurrentEpisodeLabels } = await import('@/hooks/use-labels')
    const { result } = renderHook(() => useCurrentEpisodeLabels(0))

    await act(async () => {
      await result.current.toggle('BAD')
    })

    expect(mockLabelStore.toggleLabel).toHaveBeenCalledWith(0, 'BAD')
  })
})
