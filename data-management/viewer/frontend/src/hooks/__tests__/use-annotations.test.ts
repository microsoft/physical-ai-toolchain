import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { _resetCsrfToken } from '@/lib/api-client'
import { useAnnotationStore, useDatasetStore, useEpisodeStore } from '@/stores'
import type { EpisodeAnnotation } from '@/types'

const mockFetch = vi.fn()

function jsonResponse(data: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    json: () => Promise.resolve(data),
  }
}

function mockMutationFetch(apiResponse: ReturnType<typeof jsonResponse>) {
  mockFetch
    .mockResolvedValueOnce(jsonResponse({ csrf_token: 'test-csrf-token' }))
    .mockResolvedValueOnce(apiResponse)
}

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

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
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
  mockFetch.mockReset()
  _resetCsrfToken()
  vi.stubGlobal('fetch', mockFetch)
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

    const { useEpisodeAnnotations } = await import('@/hooks/use-annotations')
    const queryClient = makeQueryClient()
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children)

    selectDataset()

    renderHook(() => useEpisodeAnnotations('me'), { wrapper })

    await waitFor(() => {
      expect(useAnnotationStore.getState().currentAnnotation).not.toBeNull()
    })
    expect(useAnnotationStore.getState().currentAnnotation?.annotatorId).toBe('me')
  })

  it('initializes a new annotation when no entry matches the annotator', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ annotations: [makeAnnotation('someone-else')] }))

    const { useEpisodeAnnotations } = await import('@/hooks/use-annotations')
    const queryClient = makeQueryClient()
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children)

    selectDataset()

    renderHook(() => useEpisodeAnnotations('me'), { wrapper })

    await waitFor(() => {
      expect(useAnnotationStore.getState().currentAnnotation).not.toBeNull()
    })
    expect(useAnnotationStore.getState().annotatorId).toBe('me')
  })

  it('does not fetch when no dataset is selected', async () => {
    const { useEpisodeAnnotations } = await import('@/hooks/use-annotations')
    const queryClient = makeQueryClient()
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children)

    renderHook(() => useEpisodeAnnotations('me'), { wrapper })

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(mockFetch).not.toHaveBeenCalled()
  })
})

describe('useSaveAnnotation', () => {
  it('sends X-CSRF-Token header and marks annotation saved on success', async () => {
    const { useSaveAnnotation } = await import('@/hooks/use-annotations')
    const queryClient = makeQueryClient()
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children)

    const annotation = makeAnnotation('me')
    mockMutationFetch(jsonResponse({ annotations: [annotation] }))

    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const { result } = renderHook(() => useSaveAnnotation(), { wrapper })

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
    const { useSaveAnnotation } = await import('@/hooks/use-annotations')
    const queryClient = makeQueryClient()
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children)

    mockFetch
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'test-csrf-token' }))
      .mockResolvedValueOnce(jsonResponse({ code: 'BOOM', message: 'save failed' }, 500))

    const { result } = renderHook(() => useSaveAnnotation(), {
      wrapper,
    })

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
})

describe('useSaveCurrentAnnotation', () => {
  it('does nothing when no current annotation is set', async () => {
    const { useSaveCurrentAnnotation } = await import('@/hooks/use-annotations')
    const queryClient = makeQueryClient()
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children)

    selectDataset()

    const { result } = renderHook(() => useSaveCurrentAnnotation(), { wrapper })

    act(() => {
      result.current.save()
    })

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('saves the store annotation when prerequisites are present', async () => {
    const { useSaveCurrentAnnotation } = await import('@/hooks/use-annotations')
    const queryClient = makeQueryClient()
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children)

    selectDataset()
    useAnnotationStore.getState().loadAnnotation(makeAnnotation('me'))
    mockMutationFetch(jsonResponse({ annotations: [makeAnnotation('me')] }))

    const { result } = renderHook(() => useSaveCurrentAnnotation(), { wrapper })

    act(() => {
      result.current.save()
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockFetch.mock.calls[1][0]).toBe('/api/datasets/ds-1/episodes/0/annotations')
  })
})

describe('useDeleteAnnotation', () => {
  it('clears the annotation store and invalidates queries when annotatorId is omitted', async () => {
    const { useDeleteAnnotation } = await import('@/hooks/use-annotations')
    const queryClient = makeQueryClient()
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children)

    useAnnotationStore.getState().loadAnnotation(makeAnnotation('me'))
    mockMutationFetch(jsonResponse({ annotations: [] }))

    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    const { result } = renderHook(() => useDeleteAnnotation(), { wrapper })

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
    const { useDeleteAnnotation } = await import('@/hooks/use-annotations')
    const queryClient = makeQueryClient()
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children)

    useAnnotationStore.getState().loadAnnotation(makeAnnotation('me'))
    mockMutationFetch(jsonResponse({ annotations: [] }))

    const { result } = renderHook(() => useDeleteAnnotation(), { wrapper })

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
    const { useAutoAnalysis } = await import('@/hooks/use-annotations')
    const queryClient = makeQueryClient()
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children)

    useAnnotationStore.getState().loadAnnotation(makeAnnotation('me'))
    mockMutationFetch(jsonResponse({ suggestedRating: 3, flags: ['jerky'] }))

    const { result } = renderHook(() => useAutoAnalysis(), { wrapper })

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

    const { useAnnotationSummary } = await import('@/hooks/use-annotations')
    const queryClient = makeQueryClient()
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children)

    const { result } = renderHook(() => useAnnotationSummary('ds-1'), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockFetch.mock.calls[0][0]).toBe('/api/datasets/ds-1/annotations/summary')
  })

  it('is disabled when datasetId is undefined', async () => {
    const { useAnnotationSummary } = await import('@/hooks/use-annotations')
    const queryClient = makeQueryClient()
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children)

    renderHook(() => useAnnotationSummary(undefined), { wrapper })

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(mockFetch).not.toHaveBeenCalled()
  })
})
