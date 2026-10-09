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
import { useEpisodeEdits, useSaveEpisodeEdits } from '@/hooks/use-episode-edits'
import { clearPersistedEditDraftsForTests } from '@/lib/edit-draft-storage'
import { useAnnotationStore, useDatasetStore, useEpisodeStore } from '@/stores'
import { useEditStore } from '@/stores/edit-store'
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

vi.mock('@/lib/edit-draft-storage', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/edit-draft-storage')>()),
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

beforeEach(async () => {
  useEditStore.getState().clear()
  await clearPersistedEditDraftsForTests()
  installFetchMock({ csrf: false })
  useDatasetStore.getState().reset()
  useAnnotationStore.getState().clear()
  useEpisodeStore.getState().reset()
  draftMocks.load.mockReset()
  draftMocks.persist.mockReset()
  draftMocks.persist.mockResolvedValue(undefined)
  draftMocks.load.mockResolvedValue(undefined)
  principalMocks.fetch.mockReset()
  principalMocks.fetch.mockResolvedValue({ scopeId: 'me', authMode: 'local' })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('saved episode edits', () => {
  it('retains an annotation draft as a conflict when the source generation changes', async () => {
    selectDataset()
    useEpisodeStore.setState({
      currentEpisode: {
        meta: { index: 0, length: 10, taskIndex: 0, hasAnnotations: true },
        sourceId: 'source-a',
        sourceRevision: 'generation-new',
        cameras: [],
        videoUrls: {},
        trajectoryData: [],
      },
    })
    const baseline = makeAnnotation('me')
    const draft = { ...baseline, notes: 'Unsaved work' }
    draftMocks.load.mockResolvedValue({
      baseline,
      draft,
      baseEtag: 'same-revision',
      sourceScopes: { 0: { sourceId: 'source-a', sourceRevision: 'generation-old' } },
    })
    mockFetch.mockResolvedValue(
      jsonResponse(
        { dataset_id: 'ds-1', episode_index: 0, annotations: [baseline] },
        { headers: { ETag: 'same-revision' } },
      ),
    )
    const { result } = renderHookWithProviders(() => ({
      read: useEpisodeAnnotations(),
      save: useSaveAnnotation(),
    }))
    await waitFor(() => expect(useAnnotationStore.getState().draftHydrated).toBe(true))
    expect(useAnnotationStore.getState().currentAnnotation?.notes).toBe('Unsaved work')
    expect(useAnnotationStore.getState().conflict).toMatchObject({ sourceChanged: true })
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ annotations: [baseline] }, { headers: { ETag: 'changed' } }),
    )
    await act(async () => {
      await result.current.read.refetch()
    })
    expect(useAnnotationStore.getState().conflict).toMatchObject({ sourceChanged: true })
    act(() => useAnnotationStore.setState({ conflict: null }))
    mockFetch.mockClear()
    await act(async () => {
      await expect(
        result.current.save.mutateAsync({ datasetId: 'ds-1', episodeIndex: 0, annotation: draft }),
      ).rejects.toThrow(/source/i)
    })
    expect(mockFetch).not.toHaveBeenCalled()
  })

  const operations = {
    datasetId: 'ds-1',
    episodeIndex: 0,
    globalTransform: { colorAdjustment: { brightness: 0.2 } },
    cameraTransforms: { wrist_camera: { resize: { width: 32, height: 24 } } },
    removedFrames: [2],
  }
  const state = {
    source_id: 'source-a',
    source_revision: 'generation-a',
    author_id: 'me',
    saved: {
      schema_version: '1.0.0',
      dataset_id: 'ds-1',
      episode_index: 0,
      source_id: 'source-a',
      source_revision: 'generation-a',
      author_id: 'me',
      operations,
    },
  }

  it('hydrates server edits without renaming camera keys and saves against the loaded revision', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(state, { headers: { ETag: 'revision-a' } }))
    const { result } = renderHookWithProviders(() => ({
      read: useEpisodeEdits('ds-1', 0, 'me'),
      save: useSaveEpisodeEdits(),
    }))
    await waitFor(() => expect(result.current.read.isReady).toBe(true))
    expect(useEditStore.getState().cameraTransforms.wrist_camera.resize?.width).toBe(32)
    act(() => useEditStore.getState().toggleFrameRemoval(5))
    const submitted = {
      ...useEditStore.getState().serverBaseline!,
      operations: useEditStore.getState().getEditOperations()!,
    }
    mockMutationFetch(
      jsonResponse(
        {
          ...state,
          saved: {
            ...state.saved,
            operations: {
              ...submitted.operations,
              globalTransform: {
                colorAdjustment: { brightness: 0.2, contrast: 0, saturation: 0, gamma: 1, hue: 0 },
              },
            },
          },
        },
        { headers: { ETag: 'revision-b' } },
      ),
    )
    await act(async () => {
      await result.current.save.mutateAsync(submitted)
    })
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/datasets/ds-1/episodes/0/edits',
      expect.objectContaining({
        method: 'PUT',
        headers: expect.objectContaining({ 'If-Match': 'revision-a' }),
      }),
    )
    expect(useEditStore.getState().serverBaseline?.etag).toBe('revision-b')
    expect(useEditStore.getState().isDirty).toBe(false)
  })

  it('does not acknowledge a write without a server revision', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ ...state, saved: null }))
    const { result } = renderHookWithProviders(() => ({
      read: useEpisodeEdits('ds-1', 0, 'me'),
      save: useSaveEpisodeEdits(),
    }))
    await waitFor(() => expect(result.current.read.isReady).toBe(true))
    act(() => useEditStore.getState().toggleFrameRemoval(5))
    const submitted = {
      ...useEditStore.getState().serverBaseline!,
      operations: useEditStore.getState().getEditOperations()!,
    }
    mockMutationFetch(
      jsonResponse({ ...state, saved: { ...state.saved, operations: submitted.operations } }),
    )
    await act(async () => {
      await expect(result.current.save.mutateAsync(submitted)).rejects.toThrow('revision')
    })
    expect(mockFetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        headers: expect.objectContaining({ 'If-None-Match': '*' }),
      }),
    )
    expect(useEditStore.getState().isDirty).toBe(true)
    expect(useEditStore.getState().serverBaseline?.etag).toBeNull()
  })

  it('rejects a read scoped to a different server author', async () => {
    mockFetch.mockResolvedValue(jsonResponse({ ...state, author_id: 'someone-else' }))
    const { result } = renderHookWithProviders(() => useEpisodeEdits('ds-1', 0, 'me'))
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.isReady).toBe(false)
    expect(useEditStore.getState().serverBaseline).toBeNull()
  })
})

describe('useEpisodeAnnotations', () => {
  it('never persists the previous episode draft under a newly hydrated episode key', async () => {
    selectDataset()
    const saved = makeAnnotation('me')
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ annotations: [saved] }, { headers: { ETag: 'a' } }),
    )
    const { queryClient } = renderHookWithProviders(() => useEpisodeAnnotations())
    await waitFor(() => expect(useAnnotationStore.getState().draftHydrated).toBe(true))
    act(() => useAnnotationStore.getState().updateNotes('Episode A draft'))
    await waitFor(() =>
      expect(draftMocks.persist).toHaveBeenCalledWith(
        'ds-1',
        0,
        'me',
        expect.objectContaining({ draft: expect.objectContaining({ notes: 'Episode A draft' }) }),
      ),
    )
    queryClient.setQueryData(annotationKeys.detail('ds-1', 1, 'me'), {
      data: { annotations: [{ ...saved, notes: 'Episode B' }] },
      etag: 'b',
    })
    act(() => useEpisodeStore.setState({ currentIndex: 1 }))
    await waitFor(() =>
      expect(useAnnotationStore.getState().currentAnnotation?.notes).toBe('Episode B'),
    )
    expect(
      draftMocks.persist.mock.calls.some(
        ([, index, , draft]) => index === 1 && draft?.draft.notes === 'Episode A draft',
      ),
    ).toBe(false)
  })

  it('preserves dirty content and its baseline when a refreshed server revision changes', async () => {
    selectDataset()
    const saved = makeAnnotation('me')
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ annotations: [saved] }, { headers: { ETag: 'one' } }),
    )
    const { result } = renderHookWithProviders(() => useEpisodeAnnotations())
    await waitFor(() => expect(useAnnotationStore.getState().currentAnnotation).not.toBeNull())
    await waitFor(() => expect(draftMocks.load).toHaveBeenCalled())
    act(() => useAnnotationStore.getState().updateNotes('My unsaved draft'))
    mockFetch.mockResolvedValueOnce(
      jsonResponse(
        { annotations: [{ ...saved, notes: 'Remote edit' }] },
        { headers: { ETag: 'two' } },
      ),
    )
    await act(async () => {
      await result.current.refetch()
    })
    await waitFor(() => expect(result.current.data?.etag).toBe('two'))
    expect(useAnnotationStore.getState().currentAnnotation?.notes).toBe('My unsaved draft')
    expect(useAnnotationStore.getState().originalAnnotation?.notes).toBe('')
    expect(useAnnotationStore.getState().baseEtag).toBe('one')
    expect(useAnnotationStore.getState().conflict?.currentEtag).toBe('two')
  })

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
  it('does not resurrect the pre-save draft as a conflict after a successful save', async () => {
    selectDataset()
    const saved = makeAnnotation('me')
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ annotations: [saved] }, { headers: { ETag: '"one"' } }),
    )
    const { result } = renderHookWithProviders(() => ({
      read: useEpisodeAnnotations(),
      save: useSaveCurrentAnnotation(),
    }))
    await waitFor(() => expect(useAnnotationStore.getState().draftHydrated).toBe(true))
    act(() => useAnnotationStore.getState().updateNotes('Saved note'))
    const edited = useAnnotationStore.getState().currentAnnotation!
    // Storage still holds the pre-save draft until its asynchronous deletion runs.
    draftMocks.load.mockResolvedValue({ baseline: saved, draft: edited, baseEtag: '"one"' })
    mockMutationFetch(jsonResponse({ annotations: [edited] }, { headers: { ETag: '"two"' } }))
    await act(async () => {
      await result.current.save.save()
    })
    await waitFor(() => expect(draftMocks.persist).toHaveBeenLastCalledWith('ds-1', 0, 'me', null))
    expect(useAnnotationStore.getState()).toMatchObject({
      conflict: null,
      isDirty: false,
      baseEtag: '"two"',
    })
  })

  it('discards a recovered draft that already matches the saved server annotation', async () => {
    selectDataset()
    const saved = { ...makeAnnotation('me'), notes: 'Saved note' }
    draftMocks.load.mockResolvedValue({
      baseline: makeAnnotation('me'),
      draft: saved,
      baseEtag: '"one"',
    })
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ annotations: [saved] }, { headers: { ETag: '"two"' } }),
    )
    renderHookWithProviders(() => useEpisodeAnnotations())
    await waitFor(() => expect(useAnnotationStore.getState().draftHydrated).toBe(true))
    expect(useAnnotationStore.getState()).toMatchObject({
      conflict: null,
      isDirty: false,
      baseEtag: '"two"',
    })
    await waitFor(() => expect(draftMocks.persist).toHaveBeenLastCalledWith('ds-1', 0, 'me', null))
  })

  it('does not fetch when no dataset is selected', async () => {
    renderHookWithProviders(() => useEpisodeAnnotations())

    await Promise.resolve()
    expect(mockFetch).not.toHaveBeenCalled()
  })
})

describe('useSaveAnnotation', () => {
  it('rejects a stale save callback after another annotation resource is active', async () => {
    const annotation = makeAnnotation('me')
    useAnnotationStore.getState().loadAnnotation(annotation)
    useAnnotationStore.setState({
      resourceKey: JSON.stringify(['ds-1', 1, 'me']),
      draftHydrated: true,
    })
    mockMutationFetch(jsonResponse({ annotations: [annotation] }, { headers: { ETag: 'saved' } }))
    const { result } = renderHookWithProviders(() => useSaveAnnotation())
    await act(async () => {
      await expect(
        result.current.mutateAsync({ datasetId: 'ds-1', episodeIndex: 0, annotation }),
      ).rejects.toThrow(/active annotation/i)
    })
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('excludes duplicate annotation writes across separate Save owners', async () => {
    const annotation = makeAnnotation('me')
    useAnnotationStore.getState().loadAnnotation(annotation)
    let complete!: (response: JsonResponseLike) => void
    mockFetch
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'test-csrf-token' }))
      .mockReturnValueOnce(
        new Promise((resolve) => {
          complete = resolve
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ annotations: [annotation] }, { headers: { ETag: 'duplicate' } }),
      )
    const { result } = renderHookWithProviders(() => ({
      first: useSaveAnnotation(),
      second: useSaveAnnotation(),
    }))
    const request = { datasetId: 'ds-1', episodeIndex: 0, annotation }
    act(() => result.current.first.mutate(request))
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2))
    act(() => result.current.second.mutate(request))
    await waitFor(() =>
      expect(result.current.second.isSuccess || result.current.second.isError).toBe(true),
    )
    complete(jsonResponse({ annotations: [annotation] }, { headers: { ETag: 'saved' } }))
    await waitFor(() => expect(result.current.first.isSuccess).toBe(true))
    expect(mockFetch).toHaveBeenCalledTimes(2)
    expect(result.current.second.isError).toBe(true)
  })

  it('retains a dirty annotation when the save response has no revision', async () => {
    const annotation = makeAnnotation('me')
    useAnnotationStore.getState().loadAnnotation(annotation)
    useAnnotationStore.getState().updateNotes('unsaved')
    mockMutationFetch(jsonResponse({ annotations: [annotation] }))
    const { result } = renderHookWithProviders(() => useSaveAnnotation())
    act(() => {
      result.current.mutate({
        datasetId: 'ds-1',
        episodeIndex: 0,
        annotation: structuredClone(useAnnotationStore.getState().currentAnnotation!),
      })
    })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(useAnnotationStore.getState().isDirty).toBe(true)
    expect(useAnnotationStore.getState().currentAnnotation?.notes).toBe('unsaved')
  })

  it('sends X-CSRF-Token header and marks annotation saved on success', async () => {
    const annotation = makeAnnotation('me')
    mockMutationFetch(jsonResponse({ annotations: [annotation] }, { headers: { ETag: 'saved' } }))

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
    resolveFetch(jsonResponse({ annotations: [annotation] }, { headers: { ETag: 'saved' } }))
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

  it('does not acknowledge an old episode into the newly selected annotation context', async () => {
    selectDataset('ds-1', 0)
    const submitted = makeAnnotation('me')
    useAnnotationStore.getState().loadAnnotation(submitted)
    let complete!: (response: JsonResponseLike) => void
    mockFetch
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'test-csrf-token' }))
      .mockReturnValueOnce(
        new Promise((resolve) => {
          complete = resolve
        }),
      )
    const { result } = renderHookWithProviders(() => useSaveAnnotation())
    act(() => {
      result.current.mutate({ datasetId: 'ds-1', episodeIndex: 0, annotation: submitted })
    })
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2))
    act(() => {
      useEpisodeStore.setState({ currentIndex: 1 })
      useAnnotationStore
        .getState()
        .loadAnnotation({ ...makeAnnotation('me'), notes: 'Episode B baseline' })
      useAnnotationStore.getState().updateNotes('Episode B draft')
    })
    complete(jsonResponse({ annotations: [submitted] }, { headers: { ETag: 'saved-a' } }))
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(useAnnotationStore.getState().originalAnnotation?.notes).toBe('Episode B baseline')
    expect(useAnnotationStore.getState().currentAnnotation?.notes).toBe('Episode B draft')
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
    mockMutationFetch(
      jsonResponse({ annotations: [makeAnnotation('me')] }, { headers: { ETag: 'saved' } }),
    )

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
