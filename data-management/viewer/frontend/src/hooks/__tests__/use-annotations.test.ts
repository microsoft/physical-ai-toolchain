import { act, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  annotationKeys,
  useAnnotationSummary,
  useAutoAnalysis,
  useDeleteAnnotation,
  useEpisodeAnnotations,
  useSaveAnnotation,
  useSaveCurrentAnnotation,
} from '@/hooks/use-annotations'
import { useAnnotationStore, useDatasetStore, useEpisodeStore } from '@/stores'
import {
  installFetchMock,
  jsonResponse,
  type JsonResponseLike,
  mockFetch,
  mockMutationFetch,
} from '@/test-utils/fetch-mocks'
import { renderHookWithProviders } from '@/test-utils/render'
import type { EpisodeAnnotation } from '@/types'

const draftMocks = vi.hoisted(() => ({
  load: vi.fn(),
  persist: vi.fn(),
}))
const principalMocks = vi.hoisted(() => ({
  fetch: vi.fn(),
}))

vi.mock('@/lib/edit-draft-storage', () => ({
  loadPersistedAnnotationDraft: draftMocks.load,
  persistAnnotationDraft: draftMocks.persist,
}))

vi.mock('@/lib/principal-context', () => ({
  fetchPrincipalContext: principalMocks.fetch,
}))

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
  installFetchMock({ csrf: false })
  useDatasetStore.getState().reset()
  useAnnotationStore.getState().clear()
  useEpisodeStore.getState().reset()
  draftMocks.load.mockReset()
  draftMocks.persist.mockReset()
  draftMocks.load.mockResolvedValue(undefined)
  principalMocks.fetch.mockReset()
  principalMocks.fetch.mockResolvedValue({ scopeId: 'me', authMode: 'local' })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useEpisodeAnnotations', () => {
  it('isolates annotation query keys by principal scope', () => {
    expect(annotationKeys.detail('ds-1', 0, 'principal-one')).not.toEqual(
      annotationKeys.detail('ds-1', 0, 'principal-two'),
    )
  })

  it('loads the matching annotator entry into the annotation store', async () => {
    const annotation = makeAnnotation('me')
    mockFetch.mockResolvedValueOnce(jsonResponse({ annotations: [annotation] }))

    selectDataset()

    renderHookWithProviders(() => useEpisodeAnnotations())

    await waitFor(() => {
      expect(useAnnotationStore.getState().currentAnnotation).not.toBeNull()
    })
    expect(useAnnotationStore.getState().currentAnnotation?.annotatorId).toBe('me')
  })

  it('initializes a new annotation when no entry matches the annotator', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ annotations: [makeAnnotation('someone-else')] }))

    selectDataset()

    renderHookWithProviders(() => useEpisodeAnnotations())

    await waitFor(() => {
      expect(useAnnotationStore.getState().currentAnnotation).not.toBeNull()
    })
    expect(useAnnotationStore.getState().annotatorId).toBe('me')
  })

  it('restores a persisted local draft over the server baseline', async () => {
    const baseline = makeAnnotation('me')
    const draft = { ...baseline, notes: 'Unsaved local note' }
    mockFetch.mockResolvedValueOnce(jsonResponse({ annotations: [baseline] }))
    draftMocks.load.mockResolvedValueOnce({
      schemaVersion: 2,
      principalScopeId: 'me',
      resource: { kind: 'annotation', datasetId: 'ds-1', episodeIndex: 0 },
      baseEtag: '"revision-one"',
      baseline,
      draft,
      generation: 1,
      updatedAt: '2026-09-18T00:00:00Z',
    })
    selectDataset()

    renderHookWithProviders(() => useEpisodeAnnotations())

    await waitFor(() => expect(useAnnotationStore.getState().isDirty).toBe(true))
    expect(draftMocks.load).toHaveBeenCalledWith('ds-1', 0, 'me')
    expect(useAnnotationStore.getState().currentAnnotation?.notes).toBe('Unsaved local note')
    expect(useAnnotationStore.getState().originalAnnotation?.notes).toBe('')
  })

  it('does not let delayed draft hydration overwrite a newer edit', async () => {
    const baseline = makeAnnotation('me')
    let resolveDraft!: (value: unknown) => void
    draftMocks.load.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveDraft = resolve
      }),
    )
    mockFetch.mockResolvedValueOnce(jsonResponse({ annotations: [baseline] }))
    selectDataset()
    renderHookWithProviders(() => useEpisodeAnnotations())
    await waitFor(() => expect(useAnnotationStore.getState().currentAnnotation).not.toBeNull())

    act(() => useAnnotationStore.getState().updateNotes('newer edit'))
    resolveDraft({
      schemaVersion: 2,
      principalScopeId: 'me',
      resource: { kind: 'annotation', datasetId: 'ds-1', episodeIndex: 0 },
      baseEtag: '"revision-one"',
      baseline,
      draft: { ...baseline, notes: 'older draft' },
      generation: 1,
      updatedAt: '2026-09-18T00:00:00Z',
    })
    await Promise.resolve()

    expect(useAnnotationStore.getState().currentAnnotation?.notes).toBe('newer edit')
  })
  it('does not fetch when no dataset is selected', async () => {
    renderHookWithProviders(() => useEpisodeAnnotations())

    await Promise.resolve()
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
    expect(useAnnotationStore.getState().error).toBe('The server could not complete the request')
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
    await Promise.resolve()
  })

  it('keeps post-submit edits dirty when an earlier save succeeds', async () => {
    const submitted = makeAnnotation('me')
    useAnnotationStore.getState().loadAnnotation(submitted)
    act(() => useAnnotationStore.getState().updateNotes('submitted'))
    const submittedSnapshot = structuredClone(useAnnotationStore.getState().currentAnnotation!)
    let resolveFetch!: (response: JsonResponseLike) => void
    mockFetch
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'test-csrf-token' }))
      .mockReturnValueOnce(new Promise((resolve) => (resolveFetch = resolve)))
    const { result } = renderHookWithProviders(() => useSaveAnnotation())

    act(() => {
      result.current.mutate({ datasetId: 'ds-1', episodeIndex: 0, annotation: submittedSnapshot })
    })
    act(() => useAnnotationStore.getState().updateNotes('post-submit edit'))
    resolveFetch(jsonResponse({ annotations: [submittedSnapshot] }, { headers: { ETag: '"two"' } }))
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(useAnnotationStore.getState().currentAnnotation?.notes).toBe('post-submit edit')
    expect(useAnnotationStore.getState().originalAnnotation?.notes).toBe('submitted')
    expect(useAnnotationStore.getState().isDirty).toBe(true)
  })

  it('retains a structured conflict when a stale save returns 412', async () => {
    const annotation = makeAnnotation('me')
    useAnnotationStore.getState().loadAnnotation(annotation)
    act(() => useAnnotationStore.getState().updateNotes('local draft'))
    mockFetch
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'test-csrf-token' }))
      .mockResolvedValueOnce(
        jsonResponse(
          {
            code: 'PRECONDITION_FAILED',
            message: 'Resource revision precondition failed',
            details: { currentEtag: '"revision-two"' },
          },
          412,
        ),
      )
    const { result } = renderHookWithProviders(() => useSaveAnnotation())

    act(() => {
      result.current.mutate({
        datasetId: 'ds-1',
        episodeIndex: 0,
        annotation: useAnnotationStore.getState().currentAnnotation!,
      })
    })
    await waitFor(() => expect(result.current.isError).toBe(true))

    expect(useAnnotationStore.getState().currentAnnotation?.notes).toBe('local draft')
    expect(useAnnotationStore.getState().conflict).toMatchObject({ currentEtag: '"revision-two"' })
  })
})

describe('useSaveCurrentAnnotation', () => {
  it('does nothing when no current annotation is set', async () => {
    selectDataset()

    const { result } = renderHookWithProviders(() => useSaveCurrentAnnotation())

    act(() => {
      result.current.save()
    })

    await Promise.resolve()
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
    queryClient.setQueryData(annotationKeys.detail('ds-1', 0, 'me'), {
      data: { annotations: [makeAnnotation('me')] },
      etag: '"revision-one"',
    })
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

  it('does not let a stale caller-selected annotatorId influence deletion', async () => {
    useAnnotationStore.getState().loadAnnotation(makeAnnotation('me'))
    mockMutationFetch(jsonResponse({ annotations: [] }))

    const { result, queryClient } = renderHookWithProviders(() => useDeleteAnnotation())
    queryClient.setQueryData(annotationKeys.detail('ds-1', 0, 'me'), {
      data: { annotations: [makeAnnotation('me')] },
      etag: '"revision-one"',
    })

    const staleRequest = { datasetId: 'ds-1', episodeIndex: 0, annotatorId: 'someone-else' }
    act(() => result.current.mutate(staleRequest))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(useAnnotationStore.getState().currentAnnotation).toBeNull()
    expect(mockFetch.mock.calls[1][0]).not.toContain('annotator_id')
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

    await Promise.resolve()
    expect(mockFetch).not.toHaveBeenCalled()
  })
})
