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
import { installFetchMock, jsonResponse, mockFetch } from '@/test-utils/fetch-mocks'
import { renderHookWithProviders } from '@/test-utils/render-hook'

function selectDataset(id = 'ds-1') {
  const dataset = {
    id,
    name: 'Dataset 1',
    totalEpisodes: 1,
    fps: 30,
    features: {},
    tasks: [],
  }
  useDatasetStore.getState().setDatasets([dataset as never])
  useDatasetStore.getState().selectDataset(id)
}

beforeEach(() => {
  installFetchMock()
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

      await new Promise((resolve) => setTimeout(resolve, 0))
      expect(mockFetch).not.toHaveBeenCalled()
    })
  })

  describe('useSaveEpisodeLabels', () => {
    it('PUTs labels and commits them to the store', async () => {
      mockFetch.mockResolvedValueOnce(jsonResponse({ episode_index: 0, labels: ['SUCCESS'] }))

      selectDataset('ds-1')

      const { result } = renderHookWithProviders(() => useSaveEpisodeLabels())

      await act(async () => {
        await result.current.mutateAsync({
          episodeIdx: 0,
          labels: ['SUCCESS'],
        })
      })

      expect(mockFetch).toHaveBeenCalledTimes(1)
      const [url, init] = mockFetch.mock.calls[0]
      expect(url).toBe('/api/datasets/ds-1/episodes/0/labels')
      expect(init.method).toBe('PUT')
      expect(JSON.parse(init.body)).toEqual({ labels: ['SUCCESS'] })

      expect(useLabelStore.getState().episodeLabels[0]).toEqual(['SUCCESS'])
    })
  })

  describe('useAddLabelOption', () => {
    it('POSTs new label option', async () => {
      mockFetch.mockResolvedValueOnce(jsonResponse(['SUCCESS', 'NEW']))

      selectDataset('ds-1')

      const { result } = renderHookWithProviders(() => useAddLabelOption())

      await act(async () => {
        await result.current.mutateAsync('new')
      })

      expect(mockFetch).toHaveBeenCalledTimes(1)
      const [url, init] = mockFetch.mock.calls[0]
      expect(url).toBe('/api/datasets/ds-1/labels/options')
      expect(init.method).toBe('POST')
      expect(JSON.parse(init.body)).toEqual({ label: 'new' })
    })
  })

  describe('useRemoveLabelOption', () => {
    it('DELETEs label option using uppercased path', async () => {
      mockFetch.mockResolvedValueOnce(jsonResponse(['SUCCESS']))

      selectDataset('ds-1')

      const { result } = renderHookWithProviders(() => useRemoveLabelOption())

      await act(async () => {
        await result.current.mutateAsync('custom')
      })

      expect(mockFetch).toHaveBeenCalledTimes(1)
      const [url, init] = mockFetch.mock.calls[0]
      expect(url).toBe('/api/datasets/ds-1/labels/options/CUSTOM')
      expect(init.method).toBe('DELETE')
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
