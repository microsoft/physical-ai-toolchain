/**
 * TanStack Query hook for joint configuration fetch and persistence.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect } from 'react'

import { apiRequest } from '@/lib/api-client'
import { useDatasetStore } from '@/stores'
import { type JointConfig, useJointConfigStore } from '@/stores/joint-config-store'

function toApiPayload(config: JointConfig) {
  return { labels: config.labels, groups: config.groups }
}

async function fetchJointConfig(datasetId: string): Promise<JointConfig> {
  return apiRequest<JointConfig>(`/datasets/${datasetId}/joint-config`)
}

export async function saveJointConfigApi(
  datasetId: string,
  config: JointConfig,
): Promise<JointConfig> {
  return apiRequest<JointConfig>(`/datasets/${datasetId}/joint-config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(toApiPayload(config)),
  })
}

async function fetchJointConfigDefaults(): Promise<JointConfig> {
  return apiRequest<JointConfig>('/joint-config/defaults')
}

export async function saveJointConfigDefaultsApi(config: JointConfig): Promise<JointConfig> {
  return apiRequest<JointConfig>('/joint-config/defaults', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(toApiPayload(config)),
  })
}

export const jointConfigKeys = {
  all: ['joint-config'] as const,
  dataset: (datasetId: string) => [...jointConfigKeys.all, datasetId] as const,
  defaults: () => [...jointConfigKeys.all, 'defaults'] as const,
}

export function useJointConfig() {
  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const setConfig = useJointConfigStore((state) => state.setConfig)

  const query = useQuery({
    queryKey: jointConfigKeys.dataset(currentDataset?.id ?? ''),
    queryFn: () => fetchJointConfig(currentDataset!.id),
    enabled: !!currentDataset,
    staleTime: 30 * 1000,
  })

  useEffect(() => {
    if (query.data) {
      setConfig(query.data)
    }
  }, [query.data, setConfig])

  return query
}

export function useSaveJointConfig() {
  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const config = useJointConfigStore((state) => state.config)
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: () => {
      if (!currentDataset) throw new Error('No dataset selected')
      return saveJointConfigApi(currentDataset.id, config)
    },
    onSuccess: () => {
      if (currentDataset) {
        queryClient.invalidateQueries({
          queryKey: jointConfigKeys.dataset(currentDataset.id),
        })
      }
    },
  })

  const save = useCallback(
    (onSuccess?: () => void) => {
      if (!currentDataset) return

      mutation.mutate(undefined, {
        onSuccess: () => {
          onSuccess?.()
        },
      })
    },
    [currentDataset, mutation],
  )

  return { save, ...mutation }
}

export function useJointConfigDefaults() {
  return useQuery({
    queryKey: jointConfigKeys.defaults(),
    queryFn: fetchJointConfigDefaults,
    staleTime: 5 * 60 * 1000,
  })
}

export function useSaveJointConfigDefaults() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (config: JointConfig) => saveJointConfigDefaultsApi(config),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: jointConfigKeys.defaults() })
    },
  })
}
