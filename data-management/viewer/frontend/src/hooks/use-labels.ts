/**
 * TanStack Query hooks for episode label operations.
 */

import { type QueryClient, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef } from 'react'

import {
  ApiClientError,
  apiRequestVersioned,
  type MutationPrecondition,
  mutationPreconditionHeaders,
  preserveProvenance,
  setEpisodeLabels,
  type VersionedResource,
} from '@/lib/api-client'
import { loadPersistedLabelDraft, persistLabelDraft } from '@/lib/edit-draft-storage'
import { fetchPrincipalContext } from '@/lib/principal-context'
import { useDatasetStore } from '@/stores'
import { useLabelStore } from '@/stores/label-store'
import type { ContributionLedger } from '@/types/annotations'
import type { EpisodeAnalysisRecord } from '@/types/api'

interface DatasetLabelsResponse {
  datasetId: string
  availableLabels: string[]
  episodes: Record<string, string[]>
  analysis?: Record<string, EpisodeAnalysisRecord>
  provenance?: Record<string, ContributionLedger>
}

export const labelKeys = {
  all: ['labels'] as const,
  dataset: (datasetId: string) => [...labelKeys.all, datasetId] as const,
  options: (datasetId: string) => [...labelKeys.dataset(datasetId), 'options'] as const,
  episode: (datasetId: string, episodeIdx: number) =>
    [...labelKeys.dataset(datasetId), 'episode', episodeIdx] as const,
}

type VersionedDatasetLabels = VersionedResource<DatasetLabelsResponse>

function labelPrecondition(queryClient: QueryClient, datasetId: string): MutationPrecondition {
  const current = queryClient.getQueryData<VersionedDatasetLabels>(labelKeys.dataset(datasetId))
  return current?.etag ? { etag: current.etag } : { createOnly: true }
}

function retainLabelEtag(queryClient: QueryClient, datasetId: string, etag: string | null): void {
  if (!etag) return
  queryClient.setQueryData<VersionedDatasetLabels>(labelKeys.dataset(datasetId), (current) =>
    current ? { ...current, etag } : current,
  )
}

export async function fetchDatasetLabels(datasetId: string): Promise<VersionedDatasetLabels> {
  return apiRequestVersioned<DatasetLabelsResponse>(
    `/datasets/${datasetId}/labels`,
    {},
    preserveProvenance<DatasetLabelsResponse>,
  )
}

async function addLabelOption(
  datasetId: string,
  label: string,
  precondition: MutationPrecondition,
): Promise<VersionedResource<string[]>> {
  return apiRequestVersioned<string[]>(`/datasets/${datasetId}/labels/options`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...mutationPreconditionHeaders(precondition) },
    body: JSON.stringify({ label }),
  })
}

async function removeLabelOption(
  datasetId: string,
  label: string,
  precondition: MutationPrecondition,
): Promise<VersionedResource<string[]>> {
  return apiRequestVersioned<string[]>(
    `/datasets/${datasetId}/labels/options/${encodeURIComponent(label.trim().toUpperCase())}`,
    {
      method: 'DELETE',
      headers: mutationPreconditionHeaders(precondition),
    },
  )
}

/** Analysis fields that can be promoted into filterable episode labels. */
export const IMPORTABLE_ANALYSIS_FIELDS = [
  'object',
  'pick_from',
  'grasp_success',
  'place_success',
  'motion_score',
  'motion_flags',
  'source',
] as const

export type ImportableAnalysisField = (typeof IMPORTABLE_ANALYSIS_FIELDS)[number]

interface ImportAnalysisResult {
  datasetId: string
  availableLabels: string[]
  episodes: Record<string, string[]>
  field: string
  prefix: string
  labelsAdded: string[]
  episodesUpdated: number
}

async function importAnalysisLabels(
  datasetId: string,
  field: ImportableAnalysisField,
  options?: { prefix?: string; overwrite?: boolean },
  precondition?: MutationPrecondition,
): Promise<VersionedResource<ImportAnalysisResult>> {
  return apiRequestVersioned<ImportAnalysisResult>(
    `/datasets/${datasetId}/labels/import-from-analysis`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...mutationPreconditionHeaders(precondition ?? { createOnly: true }),
      },
      body: JSON.stringify({
        field,
        prefix: options?.prefix,
        overwrite: options?.overwrite ?? false,
      }),
    },
  )
}

/**
 * Hook to load and sync dataset labels with the label store.
 */
export function useDatasetLabels() {
  const principalQuery = useQuery({
    queryKey: ['auth', 'principal-context'],
    queryFn: fetchPrincipalContext,
    staleTime: Number.POSITIVE_INFINITY,
  })
  const principalScopeId = principalQuery.data?.scopeId
  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const labelDatasetId = useLabelStore((state) => state.datasetId)
  const prepareDatasetLabels = useLabelStore((state) => state.prepareDatasetLabels)
  const setAvailableLabels = useLabelStore((state) => state.setAvailableLabels)
  const setDatasetEpisodeLabels = useLabelStore((state) => state.setDatasetEpisodeLabels)
  const reconcileEpisodeLabels = useLabelStore((state) => state.reconcileEpisodeLabels)
  const setAllEpisodeAnalysis = useLabelStore((state) => state.setAllEpisodeAnalysis)
  const restoreLabelDraft = useLabelStore((state) => state.restoreLabelDraft)
  const availableLabels = useLabelStore((state) => state.availableLabels)
  const episodeLabels = useLabelStore((state) => state.episodeLabels)
  const savedEpisodeLabels = useLabelStore((state) => state.savedEpisodeLabels)
  const baseEtag = useLabelStore((state) => state.baseEtag)
  const hydratedDatasetRef = useRef<string | null>(null)
  const lastServerBodyRef = useRef<DatasetLabelsResponse | null>(null)
  const setLoaded = useLabelStore((state) => state.setLoaded)

  const query = useQuery({
    queryKey: labelKeys.dataset(currentDataset?.id ?? ''),
    queryFn: () => fetchDatasetLabels(currentDataset!.id),
    enabled: !!currentDataset && !!principalScopeId,
    staleTime: 30 * 1000,
  })
  const labelsData = query.data?.data
  const hasDatasetLabels = !!labelsData && labelsData.datasetId === currentDataset?.id

  useEffect(() => {
    prepareDatasetLabels(currentDataset?.id ?? null)
  }, [currentDataset?.id, prepareDatasetLabels])

  useEffect(() => {
    if (!labelsData || labelsData.datasetId !== currentDataset?.id || !principalScopeId) return

    const datasetId = labelsData.datasetId
    if (lastServerBodyRef.current === labelsData && hydratedDatasetRef.current === datasetId) return
    lastServerBodyRef.current = labelsData
    const existing = useLabelStore.getState()
    const dirtyEntry = Object.entries(existing.episodeLabels).find(
      ([index, labels]) =>
        JSON.stringify(labels) !== JSON.stringify(existing.savedEpisodeLabels[Number(index)] ?? []),
    )
    if (
      existing.datasetId === datasetId &&
      existing.isLoaded &&
      (dirtyEntry || existing.conflict)
    ) {
      if (dirtyEntry && existing.baseEtag !== query.data?.etag) {
        existing.setConflict(query.data?.etag ?? null, Number(dirtyEntry[0]), dirtyEntry[1])
      }
      return
    }
    useLabelStore.setState({ baseEtag: query.data?.etag ?? null })
    setAvailableLabels(labelsData.availableLabels)
    if (labelDatasetId === datasetId) {
      reconcileEpisodeLabels(datasetId, labelsData.episodes)
    } else {
      setDatasetEpisodeLabels(datasetId, labelsData.episodes)
    }
    hydratedDatasetRef.current = datasetId
    setAllEpisodeAnalysis(labelsData.analysis ?? {})
    setLoaded(true)
  }, [
    currentDataset?.id,
    labelDatasetId,
    labelsData,
    principalScopeId,
    query.data?.etag,
    reconcileEpisodeLabels,
    setAllEpisodeAnalysis,
    setAvailableLabels,
    setDatasetEpisodeLabels,
    setLoaded,
  ])

  useEffect(() => {
    const datasetId = currentDataset?.id
    if (!datasetId || !principalScopeId || !hasDatasetLabels) return

    const hydrationEditGeneration = useLabelStore.getState().editGeneration
    let active = true
    void loadPersistedLabelDraft(datasetId, principalScopeId).then((draft) => {
      if (
        !active ||
        useDatasetStore.getState().currentDataset?.id !== datasetId ||
        useLabelStore.getState().editGeneration !== hydrationEditGeneration
      ) {
        return
      }
      if (draft) {
        restoreLabelDraft(
          draft.draft.availableLabels,
          draft.draft.episodeLabels,
          draft.baseline.episodeLabels,
        )
        useLabelStore.setState({ baseEtag: draft.baseEtag })
      }
    })

    return () => {
      active = false
    }
  }, [currentDataset?.id, hasDatasetLabels, principalScopeId, restoreLabelDraft])

  useEffect(() => {
    const datasetId = currentDataset?.id
    if (!datasetId || !principalScopeId || hydratedDatasetRef.current !== datasetId) return
    const isDirty = JSON.stringify(episodeLabels) !== JSON.stringify(savedEpisodeLabels)
    void persistLabelDraft(
      datasetId,
      principalScopeId,
      isDirty
        ? {
            availableLabels,
            episodeLabels,
            savedEpisodeLabels,
            baseEtag,
          }
        : null,
    )
  }, [
    availableLabels,
    currentDataset?.id,
    episodeLabels,
    principalScopeId,
    baseEtag,
    savedEpisodeLabels,
  ])

  return query
}

/**
 * Hook to save labels for the current episode.
 */
export function useSaveEpisodeLabels() {
  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const commitSubmittedEpisodeLabels = useLabelStore((state) => state.commitSubmittedEpisodeLabels)
  const setConflict = useLabelStore((state) => state.setConflict)
  const queryClient = useQueryClient()
  const saveContext = () =>
    JSON.stringify([
      useDatasetStore.getState().currentDataset?.id,
      useLabelStore.getState().datasetId,
      useLabelStore.getState().contextGeneration,
      queryClient.getQueryData(['auth', 'principal-context']),
    ])

  const mutation = useMutation({
    onMutate: () => ({ scope: saveContext(), datasetId: currentDataset?.id }),
    mutationFn: async ({ episodeIdx, labels }: { episodeIdx: number; labels: string[] }) => {
      if (!currentDataset) throw new Error('No dataset selected')
      const state = useLabelStore.getState()
      if (state.datasetId === currentDataset.id && state.conflict) {
        throw new Error('Resolve label conflicts before saving.')
      }
      const precondition =
        state.datasetId === currentDataset.id && state.isLoaded
          ? state.baseEtag
            ? { etag: state.baseEtag }
            : { createOnly: true }
          : labelPrecondition(queryClient, currentDataset.id)
      const saved = await setEpisodeLabels(
        currentDataset.id,
        episodeIdx,
        labels,
        precondition,
        'human-edit',
      )
      if (!saved.etag || saved.data.episodeIndex !== episodeIdx) {
        throw new Error(
          'Label save acknowledgment is missing its revision or matches another episode.',
        )
      }
      return saved
    },
    onSuccess: (versioned, variables, context) => {
      if (context.scope === saveContext()) {
        commitSubmittedEpisodeLabels(
          versioned.data.episodeIndex,
          variables.labels,
          versioned.data.labels,
        )
        useLabelStore.setState({ baseEtag: versioned.etag })
      }
      if (context.datasetId) {
        retainLabelEtag(queryClient, context.datasetId, versioned.etag)
        queryClient.invalidateQueries({ queryKey: labelKeys.dataset(context.datasetId) })
      }
    },
    onError: (error, variables, context) => {
      if (!context || context.scope !== saveContext()) return
      if (error instanceof ApiClientError && error.status === 412) {
        const currentEtag =
          typeof error.details?.currentEtag === 'string' ? error.details.currentEtag : null
        setConflict(currentEtag, variables.episodeIdx, variables.labels)
      }
    },
  })

  return mutation
}

/**
 * Hook to add a new label option.
 */
export function useAddLabelOption() {
  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const setAvailableLabels = useLabelStore((state) => state.setAvailableLabels)
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: (label: string) => {
      if (!currentDataset) throw new Error('No dataset selected')
      return addLabelOption(
        currentDataset.id,
        label,
        labelPrecondition(queryClient, currentDataset.id),
      )
    },
    onSuccess: (versioned) => {
      setAvailableLabels(versioned.data)
      if (currentDataset) {
        retainLabelEtag(queryClient, currentDataset.id, versioned.etag)
        queryClient.invalidateQueries({ queryKey: labelKeys.dataset(currentDataset.id) })
      }
    },
  })

  return mutation
}

/**
 * Hook to delete a label option from the current dataset.
 */
export function useRemoveLabelOption() {
  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const removeLabelOptionInStore = useLabelStore((state) => state.removeLabelOption)
  const setAvailableLabels = useLabelStore((state) => state.setAvailableLabels)
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: (label: string) => {
      if (!currentDataset) throw new Error('No dataset selected')
      return removeLabelOption(
        currentDataset.id,
        label,
        labelPrecondition(queryClient, currentDataset.id),
      )
    },
    onSuccess: (versioned, label) => {
      removeLabelOptionInStore(label)
      setAvailableLabels(versioned.data)
      if (currentDataset) {
        retainLabelEtag(queryClient, currentDataset.id, versioned.etag)
        queryClient.invalidateQueries({ queryKey: labelKeys.dataset(currentDataset.id) })
      }
    },
  })

  return mutation
}

/**
 * Hook to import an analysis field into episode labels.
 */
export function useImportAnalysisLabels() {
  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const setAvailableLabels = useLabelStore((state) => state.setAvailableLabels)
  const reconcileEpisodeLabels = useLabelStore((state) => state.reconcileEpisodeLabels)
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: ({
      field,
      prefix,
      overwrite,
    }: {
      field: ImportableAnalysisField
      prefix?: string
      overwrite?: boolean
    }) => {
      if (!currentDataset) throw new Error('No dataset selected')
      return importAnalysisLabels(
        currentDataset.id,
        field,
        { prefix, overwrite },
        labelPrecondition(queryClient, currentDataset.id),
      )
    },
    onSuccess: (versioned) => {
      const data = versioned.data
      retainLabelEtag(queryClient, data.datasetId, versioned.etag)
      queryClient.invalidateQueries({ queryKey: labelKeys.dataset(data.datasetId) })
      if (useDatasetStore.getState().currentDataset?.id !== data.datasetId) return
      setAvailableLabels(data.availableLabels)
      reconcileEpisodeLabels(data.datasetId, data.episodes)
    },
  })

  return mutation
}

/**
 * Helper hook that returns the current episode's labels and a toggle function.
 */
export function useCurrentEpisodeLabels(episodeIndex: number) {
  const episodeLabels = useLabelStore((state) => state.episodeLabels)
  const toggleLabel = useLabelStore((state) => state.toggleLabel)

  const currentLabels = episodeLabels[episodeIndex] || []

  const toggle = useCallback(
    async (label: string) => {
      toggleLabel(episodeIndex, label)
    },
    [episodeIndex, toggleLabel],
  )

  return { currentLabels, toggle }
}
