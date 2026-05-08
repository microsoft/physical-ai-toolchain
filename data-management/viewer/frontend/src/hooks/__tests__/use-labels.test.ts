import { act, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useAddLabelOption,
  useCurrentEpisodeLabels,
  useDatasetLabels,
  useRemoveLabelOption,
  useSaveEpisodeLabels,
} from '@/hooks/use-labels'
import { useDatasetStore } from '@/stores'
import { useLabelStore } from '@/stores/label-store'
import { renderHookWithQuery } from '@/test/render-hook-with-query'

const mockFetch = vi.fn()

function jsonResponse<T>(data: T, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    json: async () => data,
  } as Response
}

function selectDataset(id = 'ds1') {
  useDatasetStore.getState().setDatasets([
    {
      id,
      name: 'Dataset',
      path: `/data/${id}`,
      num_episodes: 0,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any,
  ])
  useDatasetStore.getState().selectDataset(id)
}

beforeEach(() => {
  mockFetch.mockReset()
  useDatasetStore.getState().reset()
  useLabelStore.getState().reset()
  vi.stubGlobal('fetch', mockFetch)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useDatasetLabels', () => {
  it('fetches labels and syncs them into the label store', async () => {
    selectDataset()
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        dataset_id: 'ds1',
        available_labels: ['SUCCESS', 'FAILURE', 'CUSTOM'],
        episodes: { '0': ['SUCCESS'], '1': ['FAILURE'] },
      }),
    )

    const { result } = renderHookWithQuery(() => useDatasetLabels())

    await waitFor(() => expect(result.current.data).toBeDefined())

    expect(mockFetch).toHaveBeenCalledWith('/api/datasets/ds1/labels')

    const labelState = useLabelStore.getState()
    expect(labelState.availableLabels).toEqual(['SUCCESS', 'FAILURE', 'CUSTOM'])
    expect(labelState.savedEpisodeLabels).toEqual({ 0: ['SUCCESS'], 1: ['FAILURE'] })
    expect(labelState.isLoaded).toBe(true)
  })

  it('is disabled until a dataset is selected', async () => {
    const { result } = renderHookWithQuery(() => useDatasetLabels())
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockFetch).not.toHaveBeenCalled()
  })
})

describe('useSaveEpisodeLabels', () => {
  it('PUTs labels and commits them to the store', async () => {
    selectDataset()
    mockFetch.mockResolvedValueOnce(jsonResponse({ episode_index: 3, labels: ['SUCCESS'] }))

    const { result } = renderHookWithQuery(() => useSaveEpisodeLabels())

    await act(async () => {
      await result.current.mutateAsync({ episodeIdx: 3, labels: ['SUCCESS'] })
    })

    const [url, init] = mockFetch.mock.calls[0]
    expect(url).toBe('/api/datasets/ds1/episodes/3/labels')
    expect((init as RequestInit).method).toBe('PUT')
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      labels: ['SUCCESS'],
    })

    const labelState = useLabelStore.getState()
    expect(labelState.episodeLabels[3]).toEqual(['SUCCESS'])
    expect(labelState.savedEpisodeLabels[3]).toEqual(['SUCCESS'])
  })

  it('throws when no dataset is selected', async () => {
    const { result } = renderHookWithQuery(() => useSaveEpisodeLabels())

    await expect(result.current.mutateAsync({ episodeIdx: 0, labels: [] })).rejects.toThrow(
      'No dataset selected',
    )
  })
})

describe('useAddLabelOption', () => {
  it('POSTs new label and updates available labels', async () => {
    selectDataset()
    mockFetch.mockResolvedValueOnce(jsonResponse(['SUCCESS', 'FAILURE', 'NEW']))

    const { result } = renderHookWithQuery(() => useAddLabelOption())

    await act(async () => {
      await result.current.mutateAsync('NEW')
    })

    const [url, init] = mockFetch.mock.calls[0]
    expect(url).toBe('/api/datasets/ds1/labels/options')
    expect((init as RequestInit).method).toBe('POST')
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ label: 'NEW' })

    expect(useLabelStore.getState().availableLabels).toEqual(['SUCCESS', 'FAILURE', 'NEW'])
  })
})

describe('useRemoveLabelOption', () => {
  it('DELETEs label option using URL-encoded uppercase name', async () => {
    selectDataset()
    useLabelStore.getState().setAvailableLabels(['SUCCESS', 'FOO BAR'])
    mockFetch.mockResolvedValueOnce(jsonResponse(['SUCCESS']))

    const { result } = renderHookWithQuery(() => useRemoveLabelOption())

    await act(async () => {
      await result.current.mutateAsync('foo bar')
    })

    const [url, init] = mockFetch.mock.calls[0]
    expect(url).toBe(`/api/datasets/ds1/labels/options/${encodeURIComponent('FOO BAR')}`)
    expect((init as RequestInit).method).toBe('DELETE')

    expect(useLabelStore.getState().availableLabels).toEqual(['SUCCESS'])
  })
})

describe('useCurrentEpisodeLabels', () => {
  it('returns current labels and toggles them in the store', async () => {
    useLabelStore.getState().setAvailableLabels(['SUCCESS', 'FAILURE'])
    useLabelStore.getState().setAllEpisodeLabels({ '5': ['SUCCESS'] })

    const { result } = renderHookWithQuery(() => useCurrentEpisodeLabels(5))
    expect(result.current.currentLabels).toEqual(['SUCCESS'])

    await act(async () => {
      await result.current.toggle('FAILURE')
    })

    expect(useLabelStore.getState().episodeLabels[5]).toContain('FAILURE')
  })

  it('returns empty array when no labels recorded', () => {
    const { result } = renderHookWithQuery(() => useCurrentEpisodeLabels(99))
    expect(result.current.currentLabels).toEqual([])
  })
})
