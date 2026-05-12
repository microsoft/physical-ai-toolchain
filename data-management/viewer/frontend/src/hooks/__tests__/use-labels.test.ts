import { act, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useAddLabelOption,
  useCurrentEpisodeLabels,
  useDatasetLabels,
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
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('use-labels hooks', () => {
  describe('useDatasetLabels', () => {
    it('fetches labels and syncs them into the label store', async () => {
      mockFetch.mockResolvedValueOnce(
        jsonResponse({
          dataset_id: 'ds-1',
          available_labels: ['SUCCESS', 'CUSTOM'],
          episodes: { '0': ['SUCCESS'], '1': ['CUSTOM'] },
        }),
      )

      selectDataset('ds-1')

      const { result } = renderHookWithProviders(() => useDatasetLabels())

      await waitFor(() => expect(result.current.isSuccess).toBe(true))

      expect(mockFetch).toHaveBeenCalledTimes(1)
      expect(mockFetch.mock.calls[0][0]).toBe('/api/datasets/ds-1/labels')

      const store = useLabelStore.getState()
      expect(store.availableLabels).toEqual(['SUCCESS', 'CUSTOM'])
      expect(store.episodeLabels[0]).toEqual(['SUCCESS'])
      expect(store.episodeLabels[1]).toEqual(['CUSTOM'])
      expect(store.isLoaded).toBe(true)
    })

    it('does not fetch when no dataset is selected', async () => {
      renderHookWithProviders(() => useDatasetLabels())

      await Promise.resolve()
      expect(mockFetch).not.toHaveBeenCalled()
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
