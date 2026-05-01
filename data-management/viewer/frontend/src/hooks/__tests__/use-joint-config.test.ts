import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { _resetCsrfToken } from '@/lib/api-client'
import { useDatasetStore } from '@/stores'
import { useJointConfigStore } from '@/stores/joint-config-store'
import { jsonResponse } from '@/test/test-utils'

const mockFetch = vi.fn()

function mockMutationFetch(apiResponse: ReturnType<typeof jsonResponse>) {
  mockFetch
    .mockResolvedValueOnce(jsonResponse({ csrf_token: 'test-csrf-token' }))
    .mockResolvedValueOnce(apiResponse)
}

beforeEach(() => {
  mockFetch.mockReset()
  _resetCsrfToken()
  vi.stubGlobal('fetch', mockFetch)
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
    mockMutationFetch(jsonResponse(responseData))

    await saveJointConfigApi('ds-1', {
      datasetId: 'ds-1',
      labels: { '0': 'X' },
      groups: [{ id: 'g1', label: 'Group', indices: [0] }],
    })

    const putCall = mockFetch.mock.calls[1]
    expect(putCall[0]).toBe('/api/datasets/ds-1/joint-config')
    expect(putCall[1].headers).toHaveProperty('X-CSRF-Token', 'test-csrf-token')
  })

  it('saveJointConfigDefaults sends X-CSRF-Token header', async () => {
    const { saveJointConfigDefaultsApi } = await import('@/hooks/use-joint-config')
    const responseData = {
      dataset_id: '_defaults',
      labels: { '0': 'X' },
      groups: [{ id: 'g1', label: 'Group', indices: [0] }],
    }
    mockMutationFetch(jsonResponse(responseData))

    await saveJointConfigDefaultsApi({
      datasetId: '_defaults',
      labels: { '0': 'X' },
      groups: [{ id: 'g1', label: 'Group', indices: [0] }],
    })

    const putCall = mockFetch.mock.calls[1]
    expect(putCall[0]).toBe('/api/joint-config/defaults')
    expect(putCall[1].headers).toHaveProperty('X-CSRF-Token', 'test-csrf-token')
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

    useJointConfigStore.getState().setConfig({
      datasetId: dataset.id,
      labels: { '0': 'Right X', '1': 'Right Y', '2': 'Right Z' },
      groups: [{ id: 'right-pos', label: 'Right Arm', indices: [0, 1, 2] }],
    })

    mockMutationFetch(
      jsonResponse({
        dataset_id: dataset.id,
        labels: { '0': 'Right X', '1': 'Right Y', '2': 'Right Z' },
        groups: [{ id: 'right-pos', label: 'Right Arm', indices: [1, 0, 2] }],
      }),
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
})
