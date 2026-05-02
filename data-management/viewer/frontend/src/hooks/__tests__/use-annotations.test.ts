import { waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  annotationKeys,
  useAnnotationSummary,
  useAutoAnalysis,
  useCurrentEpisodeAutoAnalysis,
  useDeleteAnnotation,
  useEpisodeAnnotations,
  useSaveAnnotation,
  useSaveCurrentAnnotation,
} from '@/hooks/use-annotations'
import { useAnnotationStore, useDatasetStore, useEpisodeStore } from '@/stores'
import { createTestQueryClient, renderHookWithQuery } from '@/test/render-hook-with-query'

vi.mock('@/lib/api-client', () => ({
  deleteAnnotations: vi.fn(),
  fetchAnnotations: vi.fn(),
  fetchAnnotationSummary: vi.fn(),
  saveAnnotation: vi.fn(),
  triggerAutoAnalysis: vi.fn(),
}))

import {
  deleteAnnotations,
  fetchAnnotations,
  fetchAnnotationSummary,
  saveAnnotation,
  triggerAutoAnalysis,
} from '@/lib/api-client'

const mockedDeleteAnnotations = vi.mocked(deleteAnnotations)
const mockedFetchAnnotations = vi.mocked(fetchAnnotations)
const mockedFetchAnnotationSummary = vi.mocked(fetchAnnotationSummary)
const mockedSaveAnnotation = vi.mocked(saveAnnotation)
const mockedTriggerAutoAnalysis = vi.mocked(triggerAutoAnalysis)

beforeEach(() => {
  mockedDeleteAnnotations.mockReset()
  mockedFetchAnnotations.mockReset()
  mockedFetchAnnotationSummary.mockReset()
  mockedSaveAnnotation.mockReset()
  mockedTriggerAutoAnalysis.mockReset()
  useDatasetStore.getState().reset()
  useEpisodeStore.getState().reset()
  useAnnotationStore.getState().clear()
})

afterEach(() => {
  vi.restoreAllMocks()
})

function selectDataset(id: string): void {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  useDatasetStore.getState().setDatasets([{ id, name: id } as any])
  useDatasetStore.getState().selectDataset(id)
}

function setEpisode(index: number): void {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  useEpisodeStore.getState().setCurrentEpisode({ meta: { index, length: 100 } } as any)
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function makeAnnotation(annotatorId: string, overrides: Record<string, any> = {}): any {
  return {
    annotatorId,
    overallRating: 3,
    flags: [],
    notes: '',
    trajectoryQuality: { overallScore: 3, flags: [] },
    timestamp: new Date('2024-01-01T00:00:00Z').toISOString(),
    ...overrides,
  }
}

describe('useEpisodeAnnotations', () => {
  it('is disabled when no dataset is selected', () => {
    const { result } = renderHookWithQuery(() => useEpisodeAnnotations('user-1'))
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockedFetchAnnotations).not.toHaveBeenCalled()
  })

  it('loads matching annotation into the store', async () => {
    selectDataset('ds-1')
    setEpisode(0)
    const annotation = makeAnnotation('user-1', { notes: 'good run' })
    mockedFetchAnnotations.mockResolvedValueOnce({ annotations: [annotation] })

    const { result } = renderHookWithQuery(() => useEpisodeAnnotations('user-1'))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    await waitFor(() => {
      expect(useAnnotationStore.getState().currentAnnotation?.annotatorId).toBe('user-1')
    })
    expect(useAnnotationStore.getState().currentAnnotation?.notes).toBe('good run')
  })

  it('initializes a new annotation when no match exists', async () => {
    selectDataset('ds-1')
    setEpisode(0)
    mockedFetchAnnotations.mockResolvedValueOnce({
      annotations: [makeAnnotation('someone-else')],
    })

    const { result } = renderHookWithQuery(() => useEpisodeAnnotations('user-1'))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    await waitFor(() => {
      expect(useAnnotationStore.getState().currentAnnotation?.annotatorId).toBe('user-1')
    })
  })
})

describe('useSaveAnnotation', () => {
  it('marks saved, populates the cache, and invalidates summary on success', async () => {
    const queryClient = createTestQueryClient()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    const annotation = makeAnnotation('user-1')
    mockedSaveAnnotation.mockResolvedValueOnce(annotation)

    const { result } = renderHookWithQuery(() => useSaveAnnotation(), { queryClient })

    result.current.mutate({ datasetId: 'ds-1', episodeIndex: 0, annotation })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockedSaveAnnotation).toHaveBeenCalledWith('ds-1', 0, annotation)
    expect(useAnnotationStore.getState().isDirty).toBe(false)
    expect(queryClient.getQueryData(annotationKeys.detail('ds-1', 0))).toEqual(annotation)
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: annotationKeys.summary('ds-1'),
    })
  })

  it('records the error message when the mutation fails', async () => {
    mockedSaveAnnotation.mockRejectedValueOnce(new Error('network down'))
    const annotation = makeAnnotation('user-1')

    const { result } = renderHookWithQuery(() => useSaveAnnotation())
    result.current.mutate({ datasetId: 'ds-1', episodeIndex: 0, annotation })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(useAnnotationStore.getState().error).toBe('network down')
  })
})

describe('useSaveCurrentAnnotation', () => {
  it('does nothing when context is missing', () => {
    const { result } = renderHookWithQuery(() => useSaveCurrentAnnotation())
    result.current.save()
    expect(mockedSaveAnnotation).not.toHaveBeenCalled()
  })

  it('saves the active annotation from the store', async () => {
    selectDataset('ds-1')
    setEpisode(2)
    const annotation = makeAnnotation('user-1', { notes: 'pending' })
    useAnnotationStore.getState().loadAnnotation(annotation)
    mockedSaveAnnotation.mockResolvedValueOnce(annotation)

    const { result } = renderHookWithQuery(() => useSaveCurrentAnnotation())
    result.current.save()

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockedSaveAnnotation).toHaveBeenCalledWith('ds-1', 2, annotation)
  })
})

describe('useDeleteAnnotation', () => {
  it('clears the store when deleting all annotations', async () => {
    useAnnotationStore.getState().loadAnnotation(makeAnnotation('user-1'))
    mockedDeleteAnnotations.mockResolvedValueOnce(undefined as never)

    const { result } = renderHookWithQuery(() => useDeleteAnnotation())
    result.current.mutate({ datasetId: 'ds-1', episodeIndex: 0 })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockedDeleteAnnotations).toHaveBeenCalledWith('ds-1', 0, undefined)
    expect(useAnnotationStore.getState().currentAnnotation).toBeNull()
  })

  it('keeps the store when deleting a specific annotator', async () => {
    useAnnotationStore.getState().loadAnnotation(makeAnnotation('user-1'))
    mockedDeleteAnnotations.mockResolvedValueOnce(undefined as never)

    const { result } = renderHookWithQuery(() => useDeleteAnnotation())
    result.current.mutate({ datasetId: 'ds-1', episodeIndex: 0, annotatorId: 'user-1' })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockedDeleteAnnotations).toHaveBeenCalledWith('ds-1', 0, 'user-1')
    expect(useAnnotationStore.getState().currentAnnotation?.annotatorId).toBe('user-1')
  })
})

describe('useAutoAnalysis', () => {
  it('applies suggested rating and flags to the trajectory quality', async () => {
    useAnnotationStore.getState().loadAnnotation(makeAnnotation('user-1'))
    mockedTriggerAutoAnalysis.mockResolvedValueOnce({
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      suggestedRating: 4 as any,
      flags: ['joint-jitter'],
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any)

    const { result } = renderHookWithQuery(() => useAutoAnalysis())
    result.current.mutate({ datasetId: 'ds-1', episodeIndex: 0 })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    const tq = useAnnotationStore.getState().currentAnnotation?.trajectoryQuality
    expect(tq?.overallScore).toBe(4)
    expect(tq?.flags).toEqual(['joint-jitter'])
  })
})

describe('useCurrentEpisodeAutoAnalysis', () => {
  it('does nothing when context is missing', () => {
    const { result } = renderHookWithQuery(() => useCurrentEpisodeAutoAnalysis())
    result.current.analyze()
    expect(mockedTriggerAutoAnalysis).not.toHaveBeenCalled()
  })

  it('triggers analysis for the active episode', async () => {
    selectDataset('ds-1')
    setEpisode(7)
    useAnnotationStore.getState().loadAnnotation(makeAnnotation('user-1'))
    mockedTriggerAutoAnalysis.mockResolvedValueOnce({
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      suggestedRating: 5 as any,
      flags: [],
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any)

    const { result } = renderHookWithQuery(() => useCurrentEpisodeAutoAnalysis())
    result.current.analyze()

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockedTriggerAutoAnalysis).toHaveBeenCalledWith('ds-1', 7)
  })
})

describe('useAnnotationSummary', () => {
  it('is disabled when no dataset id is provided', () => {
    const { result } = renderHookWithQuery(() => useAnnotationSummary(undefined))
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockedFetchAnnotationSummary).not.toHaveBeenCalled()
  })

  it('fetches the summary for the provided dataset', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const summary = { totalAnnotations: 4 } as any
    mockedFetchAnnotationSummary.mockResolvedValueOnce(summary)

    const { result } = renderHookWithQuery(() => useAnnotationSummary('ds-1'))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockedFetchAnnotationSummary).toHaveBeenCalledWith('ds-1')
    expect(result.current.data).toEqual(summary)
  })
})
