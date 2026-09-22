import { act, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  labelKeys,
  useAddLabelOption,
  useCurrentEpisodeLabels,
  useDatasetLabels,
  useImportAnalysisLabels,
  useRemoveLabelOption,
  useSaveEpisodeLabels,
} from '@/hooks/use-labels'
import { useDatasetStore, useLabelStore } from '@/stores'
import { TEST_CSRF_TOKEN } from '@/test-utils/constants'
import {
  installFetchMock,
  jsonResponse,
  type JsonResponseLike,
  mockFetch,
} from '@/test-utils/fetch-mocks'
import { renderHookWithProviders } from '@/test-utils/render'

const labelDraftMocks = vi.hoisted(() => ({
  load: vi.fn(),
  persist: vi.fn(),
}))
const principalMocks = vi.hoisted(() => ({ fetch: vi.fn() }))

vi.mock('@/lib/edit-draft-storage', () => ({
  loadPersistedLabelDraft: labelDraftMocks.load,
  persistLabelDraft: labelDraftMocks.persist,
}))

vi.mock('@/lib/principal-context', () => ({
  fetchPrincipalContext: principalMocks.fetch,
}))

function selectDataset(id = 'ds-1') {
  const dataset = {
    id,
    name: 'Dataset 1',
    totalEpisodes: 1,
    fps: 30,
    features: {},
    tasks: [],
  }
  useDatasetStore
    .getState()
    .setDatasets([
      dataset as unknown as Parameters<
        ReturnType<typeof useDatasetStore.getState>['setDatasets']
      >[0][number],
    ])
  useDatasetStore.getState().selectDataset(id)
}

beforeEach(() => {
  installFetchMock({ csrf: false })
  useDatasetStore.getState().reset()
  useLabelStore.getState().reset()
  labelDraftMocks.load.mockReset()
  labelDraftMocks.persist.mockReset()
  labelDraftMocks.load.mockResolvedValue(undefined)
  principalMocks.fetch.mockReset()
  principalMocks.fetch.mockResolvedValue({ scopeId: 'principal-one', authMode: 'local' })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('use-labels hooks', () => {
  describe('useDatasetLabels', () => {
    it('fetches labels and syncs them into the label store', async () => {
      mockFetch.mockResolvedValueOnce(
        jsonResponse(
          {
            dataset_id: 'ds-1',
            available_labels: ['SUCCESS', 'CUSTOM'],
            episodes: { '0': ['SUCCESS'], '1': ['CUSTOM'] },
          },
          { headers: { ETag: '"revision-one"' } },
        ),
      )

      selectDataset('ds-1')

      const { result, queryClient } = renderHookWithProviders(() => useDatasetLabels())

      await waitFor(() => expect(result.current.isSuccess).toBe(true))

      expect(mockFetch).toHaveBeenCalledTimes(1)
      expect(mockFetch.mock.calls[0][0]).toBe('/api/datasets/ds-1/labels')

      const store = useLabelStore.getState()
      expect(store.availableLabels).toEqual(['SUCCESS', 'CUSTOM'])
      expect(store.episodeLabels[0]).toEqual(['SUCCESS'])
      expect(store.episodeLabels[1]).toEqual(['CUSTOM'])
      expect(store.isLoaded).toBe(true)
      expect(queryClient.getQueryData(labelKeys.dataset('ds-1'))).toMatchObject({
        etag: '"revision-one"',
      })
    })

    it('restores a persisted label draft over the server baseline', async () => {
      mockFetch.mockResolvedValueOnce(
        jsonResponse({
          dataset_id: 'ds-1',
          available_labels: ['SUCCESS', 'CUSTOM'],
          episodes: { '0': ['SUCCESS'] },
        }),
      )
      labelDraftMocks.load.mockResolvedValueOnce({
        schemaVersion: 2,
        principalScopeId: 'principal-one',
        resource: { kind: 'labels', datasetId: 'ds-1' },
        baseEtag: '"revision-one"',
        baseline: { availableLabels: ['SUCCESS', 'CUSTOM'], episodeLabels: { 0: ['SUCCESS'] } },
        draft: { availableLabels: ['SUCCESS', 'CUSTOM'], episodeLabels: { 0: ['CUSTOM'] } },
        generation: 1,
        updatedAt: '2026-09-18T00:00:00Z',
      })
      selectDataset('ds-1')

      renderHookWithProviders(() => useDatasetLabels())

      await waitFor(() => expect(useLabelStore.getState().episodeLabels[0]).toEqual(['CUSTOM']))
      expect(useLabelStore.getState().savedEpisodeLabels[0]).toEqual(['SUCCESS'])
    })

    it('does not let delayed label draft hydration overwrite a newer local edit', async () => {
      let resolveDraft!: (value: unknown) => void
      labelDraftMocks.load.mockReturnValueOnce(
        new Promise((resolve) => {
          resolveDraft = resolve
        }),
      )
      mockFetch.mockResolvedValueOnce(
        jsonResponse({
          dataset_id: 'ds-1',
          available_labels: ['SUCCESS', 'CUSTOM'],
          episodes: { '0': ['SUCCESS'] },
        }),
      )
      selectDataset('ds-1')
      renderHookWithProviders(() => useDatasetLabels())
      await waitFor(() => expect(useLabelStore.getState().isLoaded).toBe(true))

      act(() => useLabelStore.getState().setEpisodeLabels(0, ['CUSTOM']))
      resolveDraft({
        schemaVersion: 2,
        principalScopeId: 'principal-one',
        resource: { kind: 'labels', datasetId: 'ds-1' },
        baseEtag: '"one"',
        baseline: { availableLabels: ['SUCCESS'], episodeLabels: { 0: ['SUCCESS'] } },
        draft: { availableLabels: ['SUCCESS'], episodeLabels: { 0: ['FAILURE'] } },
        generation: 1,
        updatedAt: '2026-09-18T00:00:00Z',
      })
      await Promise.resolve()

      expect(useLabelStore.getState().episodeLabels[0]).toEqual(['CUSTOM'])
    })
    it('does not fetch when no dataset is selected', async () => {
      renderHookWithProviders(() => useDatasetLabels())

      await Promise.resolve()
      expect(mockFetch).not.toHaveBeenCalled()
    })

    it('clears the previous dataset labels while the next dataset loads', async () => {
      mockFetch.mockImplementation(() => new Promise<JsonResponseLike>(() => undefined))
      selectDataset('ds-1')
      const store = useLabelStore.getState()
      store.setDatasetEpisodeLabels('ds-1', { '0': ['SUCCESS'] })
      store.setAllEpisodeAnalysis({ '0': { object: 'cube' } })
      store.setAvailableLabels(['SUCCESS', 'OBJECT: CUBE'])
      store.setFilterLabels(['OBJECT: CUBE'])
      store.setLoaded(true)
      renderHookWithProviders(() => useDatasetLabels())

      act(() => {
        selectDataset('ds-2')
      })

      await waitFor(() => expect(useLabelStore.getState().datasetId).toBe('ds-2'))
      const next = useLabelStore.getState()
      expect(next.availableLabels).toEqual(['SUCCESS', 'FAILURE', 'PARTIAL'])
      expect(next.episodeLabels).toEqual({})
      expect(next.savedEpisodeLabels).toEqual({})
      expect(next.episodeAnalysis).toEqual({})
      expect(next.filterLabels).toEqual([])
      expect(next.isLoaded).toBe(false)
    })
  })

  describe('useSaveEpisodeLabels', () => {
    it('PUTs labels and commits them to the store', async () => {
      installFetchMock({ csrf: true })
      mockFetch.mockResolvedValueOnce(jsonResponse({ episode_index: 0, labels: ['SUCCESS'] }))

      selectDataset('ds-1')

      const { result } = renderHookWithProviders(() => useSaveEpisodeLabels())

      await act(async () => {
        await result.current.mutateAsync({
          episodeIdx: 0,
          labels: ['SUCCESS'],
        })
      })

      expect(mockFetch).toHaveBeenCalledTimes(2)
      const [url, init] = mockFetch.mock.calls[1]
      expect(url).toBe('/api/datasets/ds-1/episodes/0/labels')
      expect(init.method).toBe('PUT')
      expect(JSON.parse(init.body)).toEqual({ labels: ['SUCCESS'] })
      expect(init.headers).toHaveProperty('If-None-Match', '*')

      expect(useLabelStore.getState().episodeLabels[0]).toEqual(['SUCCESS'])
    })

    it('sends X-CSRF-Token on PUT', async () => {
      installFetchMock({ csrf: true })
      mockFetch.mockResolvedValueOnce(jsonResponse({ episode_index: 0, labels: ['SUCCESS'] }))

      selectDataset('ds-1')

      const { result } = renderHookWithProviders(() => useSaveEpisodeLabels())

      await act(async () => {
        await result.current.mutateAsync({ episodeIdx: 0, labels: ['SUCCESS'] })
      })

      const putCall = mockFetch.mock.calls[1]
      expect(putCall[1].headers).toHaveProperty('X-CSRF-Token', TEST_CSRF_TOKEN)
    })

    it('exposes error state when the PUT fails', async () => {
      installFetchMock({ csrf: true })
      mockFetch.mockResolvedValueOnce(jsonResponse({ code: 'BOOM', message: 'save failed' }, 500))

      selectDataset('ds-1')

      const { result } = renderHookWithProviders(() => useSaveEpisodeLabels())

      act(() => {
        result.current.mutate({ episodeIdx: 0, labels: ['SUCCESS'] })
      })

      await waitFor(() => expect(result.current.isError).toBe(true))
      expect(result.current.error).toBeInstanceOf(Error)
    })

    it('does not throw when the consumer unmounts before the PUT resolves', async () => {
      installFetchMock({ csrf: true })
      let resolveFetch!: (response: JsonResponseLike) => void
      const deferred = new Promise<JsonResponseLike>((resolve) => {
        resolveFetch = resolve
      })
      mockFetch.mockReturnValueOnce(deferred)

      selectDataset('ds-1')

      const { result, unmount } = renderHookWithProviders(() => useSaveEpisodeLabels())

      act(() => {
        result.current.mutate({ episodeIdx: 0, labels: ['SUCCESS'] })
      })

      unmount()
      resolveFetch(jsonResponse({ episode_index: 0, labels: ['SUCCESS'] }))
      await Promise.resolve()
    })

    it('keeps post-submit label edits dirty when an earlier save succeeds', async () => {
      installFetchMock({ csrf: true })
      let resolveFetch!: (response: JsonResponseLike) => void
      mockFetch.mockReturnValueOnce(new Promise((resolve) => (resolveFetch = resolve)))
      selectDataset('ds-1')
      useLabelStore.getState().setDatasetEpisodeLabels('ds-1', { '0': ['SUCCESS'] })
      useLabelStore.getState().setEpisodeLabels(0, ['FAILURE'])
      const { result } = renderHookWithProviders(() => useSaveEpisodeLabels())

      act(() => result.current.mutate({ episodeIdx: 0, labels: ['FAILURE'] }))
      act(() => useLabelStore.getState().setEpisodeLabels(0, ['CUSTOM']))
      resolveFetch(
        jsonResponse({ episode_index: 0, labels: ['FAILURE'] }, { headers: { ETag: '"two"' } }),
      )
      await waitFor(() => expect(result.current.isSuccess).toBe(true))

      expect(useLabelStore.getState().episodeLabels[0]).toEqual(['CUSTOM'])
      expect(useLabelStore.getState().savedEpisodeLabels[0]).toEqual(['FAILURE'])
    })

    it('retains a structured label conflict after HTTP 412', async () => {
      installFetchMock({ csrf: true })
      mockFetch.mockResolvedValueOnce(
        jsonResponse(
          {
            code: 'PRECONDITION_FAILED',
            message: 'Resource revision precondition failed',
            details: { currentEtag: '"two"' },
          },
          412,
        ),
      )
      selectDataset('ds-1')
      useLabelStore.getState().setDatasetEpisodeLabels('ds-1', { '0': ['SUCCESS'] })
      useLabelStore.getState().setEpisodeLabels(0, ['FAILURE'])
      const { result } = renderHookWithProviders(() => useSaveEpisodeLabels())

      act(() => result.current.mutate({ episodeIdx: 0, labels: ['FAILURE'] }))
      await waitFor(() => expect(result.current.isError).toBe(true))

      expect(useLabelStore.getState().episodeLabels[0]).toEqual(['FAILURE'])
      expect(useLabelStore.getState().conflict).toMatchObject({
        currentEtag: '"two"',
        episodeIndex: 0,
        submittedLabels: ['FAILURE'],
      })
    })
  })

  describe('useAddLabelOption', () => {
    it('POSTs new label option', async () => {
      installFetchMock({ csrf: true })
      mockFetch.mockResolvedValueOnce(jsonResponse(['SUCCESS', 'NEW']))

      selectDataset('ds-1')

      const { result } = renderHookWithProviders(() => useAddLabelOption())

      await act(async () => {
        await result.current.mutateAsync('new')
      })

      expect(mockFetch).toHaveBeenCalledTimes(2)
      const [url, init] = mockFetch.mock.calls[1]
      expect(url).toBe('/api/datasets/ds-1/labels/options')
      expect(init.method).toBe('POST')
      expect(JSON.parse(init.body)).toEqual({ label: 'new' })
      expect(init.headers).toHaveProperty('If-None-Match', '*')
    })

    it('sends X-CSRF-Token on POST', async () => {
      installFetchMock({ csrf: true })
      mockFetch.mockResolvedValueOnce(jsonResponse(['SUCCESS', 'NEW']))

      selectDataset('ds-1')

      const { result } = renderHookWithProviders(() => useAddLabelOption())

      await act(async () => {
        await result.current.mutateAsync('new')
      })

      const postCall = mockFetch.mock.calls[1]
      expect(postCall[1].headers).toHaveProperty('X-CSRF-Token', TEST_CSRF_TOKEN)
    })

    it('exposes error state when the POST fails', async () => {
      installFetchMock({ csrf: true })
      mockFetch.mockResolvedValueOnce(jsonResponse({ code: 'BOOM', message: 'add failed' }, 500))

      selectDataset('ds-1')

      const { result } = renderHookWithProviders(() => useAddLabelOption())

      act(() => {
        result.current.mutate('new')
      })

      await waitFor(() => expect(result.current.isError).toBe(true))
      expect(result.current.error).toBeInstanceOf(Error)
    })
  })

  describe('useRemoveLabelOption', () => {
    it('DELETEs label option using uppercased path', async () => {
      installFetchMock({ csrf: true })
      mockFetch.mockResolvedValueOnce(jsonResponse(['SUCCESS']))

      selectDataset('ds-1')

      const { result } = renderHookWithProviders(() => useRemoveLabelOption())

      await act(async () => {
        await result.current.mutateAsync('custom')
      })

      expect(mockFetch).toHaveBeenCalledTimes(2)
      const [url, init] = mockFetch.mock.calls[1]
      expect(url).toBe('/api/datasets/ds-1/labels/options/CUSTOM')
      expect(init.method).toBe('DELETE')
      expect(init.headers).toHaveProperty('If-None-Match', '*')
    })

    it('sends X-CSRF-Token on DELETE', async () => {
      installFetchMock({ csrf: true })
      mockFetch.mockResolvedValueOnce(jsonResponse(['SUCCESS']))

      selectDataset('ds-1')

      const { result } = renderHookWithProviders(() => useRemoveLabelOption())

      await act(async () => {
        await result.current.mutateAsync('custom')
      })

      const deleteCall = mockFetch.mock.calls[1]
      expect(deleteCall[1].headers).toHaveProperty('X-CSRF-Token', TEST_CSRF_TOKEN)
    })

    it('exposes error state when the DELETE fails', async () => {
      installFetchMock({ csrf: true })
      mockFetch.mockResolvedValueOnce(jsonResponse({ code: 'BOOM', message: 'remove failed' }, 500))

      selectDataset('ds-1')

      const { result } = renderHookWithProviders(() => useRemoveLabelOption())

      act(() => {
        result.current.mutate('custom')
      })

      await waitFor(() => expect(result.current.isError).toBe(true))
      expect(result.current.error).toBeInstanceOf(Error)
    })
  })

  describe('useImportAnalysisLabels', () => {
    it('preserves unsaved episode edits while reconciling imported labels', async () => {
      installFetchMock({ csrf: true })
      mockFetch.mockResolvedValueOnce(
        jsonResponse({
          dataset_id: 'ds-1',
          available_labels: ['SUCCESS', 'FAILURE', 'OBJECT: CUBE', 'OBJECT: BALL'],
          episodes: {
            '0': ['SUCCESS', 'OBJECT: CUBE'],
            '1': ['FAILURE', 'OBJECT: BALL'],
          },
          field: 'object',
          prefix: 'OBJECT',
          labels_added: ['OBJECT: CUBE', 'OBJECT: BALL'],
          episodes_updated: 2,
        }),
      )
      selectDataset('ds-1')
      useLabelStore.getState().setDatasetEpisodeLabels('ds-1', {
        '0': ['SUCCESS'],
        '1': ['FAILURE'],
      })
      useLabelStore.getState().setEpisodeLabels(1, ['PARTIAL'])

      const { result } = renderHookWithProviders(() => useImportAnalysisLabels())

      await act(async () => {
        await result.current.mutateAsync({ field: 'object', prefix: 'ITEM', overwrite: true })
      })

      const [, request] = mockFetch.mock.calls[1]
      expect(JSON.parse(request.body)).toEqual({
        field: 'object',
        prefix: 'ITEM',
        overwrite: true,
      })
      const store = useLabelStore.getState()
      expect(store.episodeLabels[0]).toEqual(['SUCCESS', 'OBJECT: CUBE'])
      expect(store.episodeLabels[1]).toEqual(['OBJECT: BALL', 'PARTIAL'])
      expect(store.savedEpisodeLabels[1]).toEqual(['FAILURE', 'OBJECT: BALL'])
    })

    it('does not apply a completed import after the selected dataset changes', async () => {
      installFetchMock({ csrf: true })
      let resolveImport!: (response: JsonResponseLike) => void
      mockFetch.mockReturnValueOnce(
        new Promise<JsonResponseLike>((resolve) => {
          resolveImport = resolve
        }),
      )
      selectDataset('ds-1')
      useLabelStore.getState().setDatasetEpisodeLabels('ds-1', { '0': ['SUCCESS'] })
      const { result } = renderHookWithProviders(() => useImportAnalysisLabels())

      let pending!: Promise<unknown>
      act(() => {
        pending = result.current.mutateAsync({ field: 'object' })
      })
      selectDataset('ds-2')
      useLabelStore.getState().setDatasetEpisodeLabels('ds-2', { '0': ['PARTIAL'] })
      resolveImport(
        jsonResponse({
          dataset_id: 'ds-1',
          available_labels: ['SUCCESS', 'OBJECT: CUBE'],
          episodes: { '0': ['SUCCESS', 'OBJECT: CUBE'] },
          field: 'object',
          prefix: 'OBJECT',
          labels_added: ['OBJECT: CUBE'],
          episodes_updated: 1,
        }),
      )

      await pending

      const store = useLabelStore.getState()
      expect(store.datasetId).toBe('ds-2')
      expect(store.episodeLabels[0]).toEqual(['PARTIAL'])
      expect(store.availableLabels).toEqual(['SUCCESS', 'FAILURE', 'PARTIAL'])
    })

    it('exposes an error when analysis-label import fails', async () => {
      installFetchMock({ csrf: true })
      mockFetch.mockResolvedValueOnce(jsonResponse({ detail: 'import failed' }, 500))
      selectDataset('ds-1')

      const { result } = renderHookWithProviders(() => useImportAnalysisLabels())

      act(() => {
        result.current.mutate({ field: 'object' })
      })

      await waitFor(() => expect(result.current.isError).toBe(true))
      expect(result.current.error).toMatchObject({
        name: 'ApiClientError',
        code: 'HTTP_500',
        status: 500,
        message: 'The server could not complete the request',
      })
    })
  })

  describe('useCurrentEpisodeLabels', () => {
    it('exposes labels for the episode and toggles via the store', async () => {
      selectDataset('ds-1')
      useLabelStore.getState().setAvailableLabels(['SUCCESS', 'FAILURE'])
      useLabelStore.getState().setEpisodeLabels(0, ['SUCCESS'])

      const { result } = renderHookWithProviders(() => useCurrentEpisodeLabels(0))

      expect(result.current.currentLabels).toEqual(['SUCCESS'])
      expect(useLabelStore.getState().availableLabels).toEqual(['SUCCESS', 'FAILURE'])

      act(() => {
        result.current.toggle('FAILURE')
      })

      expect(useLabelStore.getState().episodeLabels[0]).toEqual(['SUCCESS', 'FAILURE'])
    })
  })
})
