/**
 * TanStack Query hook for joint configuration fetch and persistence.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect } from 'react'

import {
  ApiClientError,
  apiRequestVersioned,
  type MutationPrecondition,
  mutationPreconditionHeaders,
  type VersionedResource,
} from '@/lib/api-client'
import { recordDiagnosticEvent } from '@/lib/playback-diagnostics'
import { useDatasetStore } from '@/stores'
import { type JointConfig, useJointConfigStore } from '@/stores/joint-config-store'

function toApiPayload(config: JointConfig) {
  return { labels: config.labels, groups: config.groups }
}

async function fetchJointConfig(datasetId: string): Promise<VersionedResource<JointConfig>> {
  return apiRequestVersioned<JointConfig>(`/datasets/${datasetId}/joint-config`)
}

export async function saveJointConfigApi(
  datasetId: string,
  config: JointConfig,
  precondition: MutationPrecondition,
): Promise<VersionedResource<JointConfig>> {
  return saveVersionedJointConfig(`/datasets/${datasetId}/joint-config`, config, precondition)
}

async function fetchJointConfigDefaults(): Promise<VersionedResource<JointConfig>> {
  return apiRequestVersioned<JointConfig>('/joint-config/defaults')
}

export async function saveJointConfigDefaultsApi(
  config: JointConfig,
  precondition: MutationPrecondition,
): Promise<VersionedResource<JointConfig>> {
  return saveVersionedJointConfig('/joint-config/defaults', config, precondition)
}

async function saveVersionedJointConfig(
  path: string,
  config: JointConfig,
  precondition: MutationPrecondition,
): Promise<VersionedResource<JointConfig>> {
  const datasetId = config.datasetId.replace(/[\r\n]/g, '')
  recordDiagnosticEvent('persistence', 'joint-config-save-started', { datasetId })
  try {
    const saved = await apiRequestVersioned<JointConfig>(path, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...mutationPreconditionHeaders(precondition) },
      body: JSON.stringify(toApiPayload(config)),
    })
    if (!saved.etag)
      throw new Error('Settings save revision was not returned. Reload before saving again.')
    recordDiagnosticEvent('persistence', 'joint-config-save-completed', { datasetId })
    return saved
  } catch (error) {
    recordDiagnosticEvent('persistence', 'joint-config-save-failed', {
      datasetId,
      status: error instanceof ApiClientError ? error.status : null,
    })
    throw error
  }
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
    if (!query.data || query.data.data.datasetId !== currentDataset?.id) return
    const current = useJointConfigStore.getState()
    if (
      current.config.datasetId === currentDataset.id &&
      current.savedConfig &&
      current.config !== current.savedConfig
    )
      return
    setConfig(query.data.data, query.data.etag)
  }, [query.data, currentDataset?.id, setConfig])

  return { ...query, data: query.data?.data }
}

export function useSaveJointConfig() {
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: ({ config, etag }: { config: JointConfig; etag: string | null | undefined }) => {
      if (etag === undefined) throw new Error('Load joint configuration before saving')
      return saveJointConfigApi(config.datasetId, config, etag ? { etag } : { createOnly: true })
    },
    onSuccess: (saved, submitted) => {
      useJointConfigStore.getState().acknowledgeSave(submitted.config, saved.data, saved.etag)
      queryClient.setQueryData(jointConfigKeys.dataset(submitted.config.datasetId), saved)
    },
  })

  const save = useCallback(
    (onSuccess?: () => void) => {
      const dataset = useDatasetStore.getState().currentDataset
      const current = useJointConfigStore.getState()
      if (!dataset || current.config.datasetId !== dataset.id) return

      mutation.mutate(
        { config: current.config, etag: current.baseEtag },
        {
          onSuccess: () => {
            onSuccess?.()
          },
        },
      )
    },
    [mutation],
  )

  return { save, ...mutation }
}

export function useJointConfigDefaults() {
  const query = useQuery({
    queryKey: jointConfigKeys.defaults(),
    queryFn: fetchJointConfigDefaults,
    staleTime: 5 * 60 * 1000,
  })
  return { ...query, data: query.data?.data, etag: query.data?.etag }
}

export function useSaveJointConfigDefaults() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ config, etag }: { config: JointConfig; etag: string | null | undefined }) => {
      if (etag === undefined) throw new Error('Load joint defaults before saving')
      return saveJointConfigDefaultsApi(config, etag ? { etag } : { createOnly: true })
    },
    onSuccess: (saved) => {
      queryClient.setQueryData(jointConfigKeys.defaults(), saved)
    },
  })
}
