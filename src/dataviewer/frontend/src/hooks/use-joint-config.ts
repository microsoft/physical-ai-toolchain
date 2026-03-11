/**
 * TanStack Query hook for joint configuration fetch and persistence.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect } from 'react'

import { mutationHeaders } from '@/lib/api-client'
import { useDatasetStore } from '@/stores'
import { type JointConfig, useJointConfigStore } from '@/stores/joint-config-store'

const API_BASE = '/api'

interface JointConfigResponse {
  dataset_id: string
  labels: Record<string, string>
  groups: { id: string; label: string; indices: number[] }[]
}

function transformResponse(data: JointConfigResponse): JointConfig {
  return {
    datasetId: data.dataset_id,
    labels: data.labels,
    groups: data.groups,
  }
}

function toApiPayload(config: JointConfig) {
  return { labels: config.labels, groups: config.groups }
}

async function fetchJointConfig(datasetId: string): Promise<JointConfig> {
  const res = await fetch(`${API_BASE}/datasets/${datasetId}/joint-config`)
  if (!res.ok) throw new Error('Failed to fetch joint config')
  return transformResponse(await res.json())
}

export async function saveJointConfigApi(datasetId: string, config: JointConfig): Promise<JointConfig> {
  const res = await fetch(`${API_BASE}/datasets/${datasetId}/joint-config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...(await mutationHeaders()) },
    body: JSON.stringify(toApiPayload(config)),
  })
  if (!res.ok) throw new Error('Failed to save joint config')
  return transformResponse(await res.json())
}

async function fetchJointConfigDefaults(): Promise<JointConfig> {
  const res = await fetch(`${API_BASE}/joint-config/defaults`)
  if (!res.ok) throw new Error('Failed to fetch joint config defaults')
  return transformResponse(await res.json())
}

export async function saveJointConfigDefaultsApi(config: JointConfig): Promise<JointConfig> {
  const res = await fetch(`${API_BASE}/joint-config/defaults`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...(await mutationHeaders()) },
    body: JSON.stringify(toApiPayload(config)),
  })
  if (!res.ok) throw new Error('Failed to save joint config defaults')
  return transformResponse(await res.json())
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
