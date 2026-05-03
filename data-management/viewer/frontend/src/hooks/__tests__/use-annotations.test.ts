import { act, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useAnnotationSummary,
  useAutoAnalysis,
  useDeleteAnnotation,
  useEpisodeAnnotations,
  useSaveAnnotation,
  useSaveCurrentAnnotation,
} from '@/hooks/use-annotations'
import { _resetCsrfToken } from '@/lib/api-client'
import { useAnnotationStore, useDatasetStore, useEpisodeStore } from '@/stores'
import {
  installFetchMock,
  jsonResponse,
  type JsonResponseLike,
  mockFetch,
  mockMutationFetch,
} from '@/test-utils/fetch-mocks'
import { renderHookWithProviders } from '@/test-utils/render-hook'
import type { EpisodeAnnotation } from '@/types'

function makeAnnotation(annotatorId: string): EpisodeAnnotation {
  return {
    annotatorId,
    timestamp: '2024-01-01T00:00:00Z',
    taskCompleteness: { rating: 'success', notes: '' },
    trajectoryQuality: { overallScore: 5, flags: [] },
    dataQuality: { rating: 'good', flags: [] },
    anomalies: [],
    notes: '',
  } as unknown as EpisodeAnnotation
}

function selectDataset(id = 'ds-1', episodeIndex = 0) {
  const dataset = {
    id,
    name: 'Dataset 1',
    totalEpisodes: 1,
    fps: 30,
    features: {},
    tasks: [],
  }
  useDatasetStore.getState().setDatasets([dataset])
  useDatasetStore.getState().selectDataset(dataset.id)
  useEpisodeStore.setState({ currentDatasetId: id, currentIndex: episodeIndex })
}

beforeEach(() => {
  installFetchMock()
  _resetCsrfToken()
  useDatasetStore.getState().reset()
  useAnnotationStore.getState().clear()
  useEpisodeStore.getState().reset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useEpisodeAnnotations', () => {
  it('loads the matching annotator entry into the annotation store', async () => {
    const annotation = makeAnnotation('me')
    mockFetch.mockResolvedValueOnce(jsonResponse({ annotations: [annotation] }))

    selectDataset()

    renderHookWithProviders(() => useEpisodeAnnotations('me'))

    await waitFor(() => {
      expect(useAnnotationStore.getState().currentAnnotation).not.toBeNull()
    })
    expect(useAnnotationStore.getState().currentAnnotation?.annotatorId).toBe('me')
  })

  it('initializes a new annotation when no entry matches the annotator', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ annotations: [makeAnnotation('someone-else')] }))

    selectDataset()

    renderHookWithProviders(() => useEpisodeAnnotations('me'))

    await waitFor(() => {
      expect(useAnnotationStore.getState().currentAnnotation).not.toBeNull()
    })
    expect(useAnnotationStore.getState().annotatorId).toBe('me')
  })

  it('does not fetch when no dataset is selected', async () => {
    renderHookWithProviders(() => useEpisodeAnnotations('me'))

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(mockFetch).not.toHaveBeenCalled()
  })
})

describe('useSaveAnnotation', () => {
  it('sends X-CSRF-Token header and marks annotation saved on success', async () => {
    const annotation = makeAnnotation('me')
    mockMutationFetch(jsonResponse({ annotations: [annotation] }))

    const { result, queryClient } = renderHookWithProviders(() => useSaveAnnotation())
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    act(() => {
      result.current.mutate({ datasetId: 'ds-1', episodeIndex: 0, annotation })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const putCall = mockFetch.mock.calls[1]
    expect(putCall[0]).toBe('/api/datasets/ds-1/episodes/0/annotations')
    expect(putCall[1].method).toBe('PUT')
    expect(putCall[1].headers).toHaveProperty('X-CSRF-Token', 'test-csrf-token')

    expect(useAnnotationStore.getState().isDirty).toBe(false)
    expect(useAnnotationStore.getState().isSaving).toBe(false)
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['annotations', 'summary', 'ds-1'],
    })
  })

  it('sets the annotation store error message when the request fails', async () => {
    mockFetch
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'test-csrf-token' }))
      .mockResolvedValueOnce(jsonResponse({ code: 'BOOM', message: 'save failed' }, 500))

    const { result } = renderHookWithProviders(() => useSaveAnnotation())

    act(() => {
      result.current.mutate({
        datasetId: 'ds-1',
        episodeIndex: 0,
        annotation: makeAnnotation('me'),
      })
    })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(useAnnotationStore.getState().error).toBe('save failed')
  })

  it('does not throw when the consumer unmounts before the request resolves', async () => {
    const annotation = makeAnnotation('me')
    let resolveFetch!: (response: JsonResponseLike) => void
    const deferred = new Promise<JsonResponseLike>((resolve) => {
      resolveFetch = resolve
    })
    mockFetch
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'test-csrf-token' }))
      .mockReturnValueOnce(deferred)

    const { result, unmount } = renderHookWithProviders(() => useSaveAnnotation())

    act(() => {
      result.current.mutate({ datasetId: 'ds-1', episodeIndex: 0, annotation })
    })

    unmount()
    resolveFetch(jsonResponse({ annotations: [annotation] }))
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
})

describe('useSaveCurrentAnnotation', () => {
  it('does nothing when no current annotation is set', async () => {
    selectDataset()

    const { result } = renderHookWithProviders(() => useSaveCurrentAnnotation())

    act(() => {
      result.current.save()
    })

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('saves the store annotation when prerequisites are present', async () => {
    selectDataset()
    useAnnotationStore.getState().loadAnnotation(makeAnnotation('me'))
    mockMutationFetch(jsonResponse({ annotations: [makeAnnotation('me')] }))

    const { result } = renderHookWithProviders(() => useSaveCurrentAnnotation())

    act(() => {
      result.current.save()
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockFetch.mock.calls[1][0]).toBe('/api/datasets/ds-1/episodes/0/annotations')
  })
})

describe('useDeleteAnnotation', () => {
  it('clears the annotation store and invalidates queries when annotatorId is omitted', async () => {
    useAnnotationStore.getState().loadAnnotation(makeAnnotation('me'))
    mockMutationFetch(jsonResponse({ annotations: [] }))

    const { result, queryClient } = renderHookWithProviders(() => useDeleteAnnotation())
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    act(() => {
      result.current.mutate({ datasetId: 'ds-1', episodeIndex: 0 })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(useAnnotationStore.getState().currentAnnotation).toBeNull()
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['annotations', 'detail', 'ds-1', 0],
    })
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['annotations', 'summary', 'ds-1'],
    })

    const deleteCall = mockFetch.mock.calls[1]
    expect(deleteCall[1].method).toBe('DELETE')
    expect(deleteCall[1].headers).toHaveProperty('X-CSRF-Token', 'test-csrf-token')
  })

  it('does not clear the store when an annotatorId is supplied', async () => {
    useAnnotationStore.getState().loadAnnotation(makeAnnotation('me'))
    mockMutationFetch(jsonResponse({ annotations: [] }))

    const { result } = renderHookWithProviders(() => useDeleteAnnotation())

    act(() => {
      result.current.mutate({
        datasetId: 'ds-1',
        episodeIndex: 0,
        annotatorId: 'someone-else',
      })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(useAnnotationStore.getState().currentAnnotation).not.toBeNull()
    expect(mockFetch.mock.calls[1][0]).toContain('annotator_id=someone-else')
  })
})

describe('useAutoAnalysis', () => {
  it('applies suggested rating and flags to the annotation store on success', async () => {
    useAnnotationStore.getState().loadAnnotation(makeAnnotation('me'))
    mockMutationFetch(jsonResponse({ suggestedRating: 3, flags: ['jerky'] }))

    const { result } = renderHookWithProviders(() => useAutoAnalysis())

    act(() => {
      result.current.mutate({ datasetId: 'ds-1', episodeIndex: 0 })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const trajectory = useAnnotationStore.getState().currentAnnotation?.trajectoryQuality
    expect(trajectory?.overallScore).toBe(3)
    expect(trajectory?.flags).toEqual(['jerky'])
  })
})

describe('useAnnotationSummary', () => {
  it('fetches the summary endpoint when datasetId is provided', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ totalEpisodes: 1, annotated: 1 }))

    const { result } = renderHookWithProviders(() => useAnnotationSummary('ds-1'))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockFetch.mock.calls[0][0]).toBe('/api/datasets/ds-1/annotations/summary')
  })

  it('is disabled when datasetId is undefined', async () => {
    renderHookWithProviders(() => useAnnotationSummary(undefined))

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(mockFetch).not.toHaveBeenCalled()
  })
})
