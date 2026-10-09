import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { enableDiagnostics } from '@/lib/playback-diagnostics'
import { useDatasetStore } from '@/stores'
import { useJointConfigStore } from '@/stores/joint-config-store'
import { TEST_CSRF_TOKEN } from '@/test-utils/constants'
import {
  installFetchMock,
  jsonResponse,
  mockFetch,
  mockMutationFetch,
} from '@/test-utils/fetch-mocks'

beforeEach(() => {
  installFetchMock({ csrf: false })
  useDatasetStore.getState().reset()
  useJointConfigStore.getState().reset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('joint config API functions', () => {
  it('saveJointConfig sends X-CSRF-Token header', async () => {
    const { saveJointConfigApi } = await import('@/hooks/use-joint-config')
    const responseData = {
      dataset_id: 'ds-1',
      labels: { '0': 'X' },
      groups: [{ id: 'g1', label: 'Group', indices: [0] }],
    }
    mockMutationFetch(jsonResponse(responseData, { headers: { ETag: '"saved"' } }))

    await saveJointConfigApi(
      'ds-1',
      {
        datasetId: 'ds-1',
        labels: { '0': 'X' },
        groups: [{ id: 'g1', label: 'Group', indices: [0] }],
      },
      { etag: '"loaded-revision"' },
    )

    const putCall = mockFetch.mock.calls[1]
    expect(putCall[0]).toBe('/api/datasets/ds-1/joint-config')
    expect(putCall[1].headers).toHaveProperty('X-CSRF-Token', TEST_CSRF_TOKEN)
    expect(putCall[1].headers).toHaveProperty('If-Match', '"loaded-revision"')
  })

  it('saveJointConfigDefaults sends X-CSRF-Token header', async () => {
    const { saveJointConfigDefaultsApi } = await import('@/hooks/use-joint-config')
    const responseData = {
      dataset_id: '_defaults',
      labels: { '0': 'X' },
      groups: [{ id: 'g1', label: 'Group', indices: [0] }],
    }
    mockMutationFetch(jsonResponse(responseData, { headers: { ETag: '"saved-defaults"' } }))

    await saveJointConfigDefaultsApi(
      {
        datasetId: '_defaults',
        labels: { '0': 'X' },
        groups: [{ id: 'g1', label: 'Group', indices: [0] }],
      },
      { createOnly: true },
    )

    const putCall = mockFetch.mock.calls[1]
    expect(putCall[0]).toBe('/api/joint-config/defaults')
    expect(putCall[1].headers).toHaveProperty('X-CSRF-Token', TEST_CSRF_TOKEN)
    expect(putCall[1].headers).toHaveProperty('If-None-Match', '*')
  })

  it('retains dirty settings and their baseline when a newer query result arrives', async () => {
    const { jointConfigKeys, useJointConfig } = await import('@/hooks/use-joint-config')
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children)
    const dataset = {
      id: 'ds-1',
      name: 'Dataset',
      totalEpisodes: 1,
      fps: 30,
      features: {},
      tasks: [],
    }
    useDatasetStore.getState().setDatasets([dataset])
    useDatasetStore.getState().selectDataset(dataset.id)
    mockFetch.mockResolvedValueOnce(
      jsonResponse(
        { dataset_id: dataset.id, labels: { '0': 'Saved' }, groups: [] },
        {
          headers: { ETag: '"base"' },
        },
      ),
    )
    renderHook(() => useJointConfig(), { wrapper })
    await waitFor(() => expect(useJointConfigStore.getState().config.labels['0']).toBe('Saved'))

    act(() => {
      useJointConfigStore.getState().updateLabel(0, 'Unsaved')
      queryClient.setQueryData(jointConfigKeys.dataset(dataset.id), {
        data: { datasetId: dataset.id, labels: { '0': 'Remote' }, groups: [] },
        etag: '"remote"',
      })
    })
    await waitFor(() =>
      expect(queryClient.getQueryData(jointConfigKeys.dataset(dataset.id))).toHaveProperty(
        'etag',
        '"remote"',
      ),
    )

    expect(useJointConfigStore.getState().config.labels['0']).toBe('Unsaved')
    expect(useJointConfigStore.getState().baseEtag).toBe('"base"')
  })

  it('useSaveJointConfig persists the latest reordered config when save runs immediately after a move', async () => {
    const { useSaveJointConfig } = await import('@/hooks/use-joint-config')
    const queryClient = new QueryClient()
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children)

    const dataset = {
      id: 'ds-1',
      name: 'Dataset 1',
      totalEpisodes: 1,
      fps: 30,
      features: {},
      tasks: [],
    }

    useDatasetStore.getState().setDatasets([dataset])
    useDatasetStore.getState().selectDataset(dataset.id)

    useJointConfigStore.getState().setConfig(
      {
        datasetId: dataset.id,
        labels: { '0': 'Right X', '1': 'Right Y', '2': 'Right Z' },
        groups: [{ id: 'right-pos', label: 'Right Arm', indices: [0, 1, 2] }],
      },
      null,
    )

    mockMutationFetch(
      jsonResponse(
        {
          dataset_id: dataset.id,
          labels: { '0': 'Right X', '1': 'Right Y', '2': 'Right Z' },
          groups: [{ id: 'right-pos', label: 'Right Arm', indices: [1, 0, 2] }],
        },
        { headers: { ETag: '"reordered"' } },
      ),
    )

    const { result } = renderHook(() => useSaveJointConfig(), { wrapper })

    act(() => {
      useJointConfigStore.getState().moveJoint(0, 'right-pos', 'right-pos', 2)
      result.current.save()
    })

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2)
    })

    const putCall = mockFetch.mock.calls[1]
    expect(JSON.parse(putCall[1].body as string)).toEqual({
      labels: { '0': 'Right X', '1': 'Right Y', '2': 'Right Z' },
      groups: [{ id: 'right-pos', label: 'Right Arm', indices: [1, 0, 2] }],
    })
  })

  it('saves defaults against the dialog revision and records conflicts without private payloads', async () => {
    const { jointConfigKeys, useSaveJointConfigDefaults } = await import('@/hooks/use-joint-config')
    const queryClient = new QueryClient()
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children)
    queryClient.setQueryData(jointConfigKeys.defaults(), {
      data: { datasetId: '_defaults', labels: {}, groups: [] },
      etag: '"newer-query"',
    })
    enableDiagnostics(['persistence'])
    window.__dataviewerDiagnostics__ = []
    mockMutationFetch(jsonResponse({ detail: 'Revision conflict' }, 412))
    const { result } = renderHook(() => useSaveJointConfigDefaults(), { wrapper })

    act(() =>
      result.current.mutate({
        config: { datasetId: '_defaults', labels: { '0': 'Private settings' }, groups: [] },
        etag: '"dialog-revision"',
      }),
    )
    await waitFor(() => expect(result.current.isError).toBe(true))

    expect(mockFetch.mock.calls[1][1].headers['If-Match']).toBe('"dialog-revision"')
    expect(window.__dataviewerDiagnostics__).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          channel: 'persistence',
          type: 'joint-config-save-failed',
          data: expect.objectContaining({ status: 412 }),
        }),
      ]),
    )
    expect(JSON.stringify(window.__dataviewerDiagnostics__)).not.toContain('Private settings')
  })

  it('does not replace settings edited after a save started or settings belonging to another dataset', () => {
    const store = useJointConfigStore.getState()
    const submitted = { datasetId: 'ds-1', labels: { '0': 'Submitted' }, groups: [] }
    store.setConfig(submitted, '"base"')
    store.updateLabel(0, 'Later edit')
    const saved = { ...submitted }
    store.acknowledgeSave(submitted, saved, '"saved"')
    expect(useJointConfigStore.getState().config.labels['0']).toBe('Later edit')
    expect(useJointConfigStore.getState().baseEtag).toBe('"saved"')

    const other = { datasetId: 'ds-2', labels: { '0': 'Other' }, groups: [] }
    store.setConfig(other, '"other"')
    store.acknowledgeSave(submitted, saved, '"late"')
    expect(useJointConfigStore.getState().config).toBe(other)
    expect(useJointConfigStore.getState().baseEtag).toBe('"other"')
  })
})
