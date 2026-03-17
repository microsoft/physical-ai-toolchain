/**
 * TanStack Query hooks for episode label operations.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect } from 'react'

import { useDatasetStore } from '@/stores'
import { useLabelStore } from '@/stores/label-store'

const API_BASE = '/api'

interface DatasetLabelsResponse {
  dataset_id: string
  available_labels: string[]
  episodes: Record<string, string[]>
}

interface EpisodeLabelsResponse {
  episode_index: number
  labels: string[]
}

export const labelKeys = {
  all: ['labels'] as const,
  dataset: (datasetId: string) => [...labelKeys.all, datasetId] as const,
  options: (datasetId: string) => [...labelKeys.dataset(datasetId), 'options'] as const,
  episode: (datasetId: string, episodeIdx: number) =>
    [...labelKeys.dataset(datasetId), 'episode', episodeIdx] as const,
}

async function fetchDatasetLabels(datasetId: string): Promise<DatasetLabelsResponse> {
  const res = await fetch(`${API_BASE}/datasets/${datasetId}/labels`)
  if (!res.ok) throw new Error('Failed to fetch labels')
  return res.json()
}

async function setEpisodeLabels(
  datasetId: string,
  episodeIdx: number,
  labels: string[],
): Promise<EpisodeLabelsResponse> {
  const res = await fetch(`${API_BASE}/datasets/${datasetId}/episodes/${episodeIdx}/labels`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ labels }),
  })
  if (!res.ok) throw new Error('Failed to save labels')
  return res.json()
}

async function addLabelOption(datasetId: string, label: string): Promise<string[]> {
  const res = await fetch(`${API_BASE}/datasets/${datasetId}/labels/options`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label }),
  })
  if (!res.ok) throw new Error('Failed to add label option')
  return res.json()
}

async function removeLabelOption(datasetId: string, label: string): Promise<string[]> {
  const res = await fetch(
    `${API_BASE}/datasets/${datasetId}/labels/options/${encodeURIComponent(label.trim().toUpperCase())}`,
    {
      method: 'DELETE',
    },
  )
  if (!res.ok) throw new Error('Failed to delete label option')
  return res.json()
}

/**
 * Hook to load and sync dataset labels with the label store.
 */
export function useDatasetLabels() {
  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const setAvailableLabels = useLabelStore((state) => state.setAvailableLabels)
  const setAllEpisodeLabels = useLabelStore((state) => state.setAllEpisodeLabels)
  const setLoaded = useLabelStore((state) => state.setLoaded)

  const query = useQuery({
    queryKey: labelKeys.dataset(currentDataset?.id ?? ''),
    queryFn: () => fetchDatasetLabels(currentDataset!.id),
    enabled: !!currentDataset,
    staleTime: 30 * 1000,
  })

  useEffect(() => {
    if (query.data) {
      setAvailableLabels(query.data.available_labels)
      setAllEpisodeLabels(query.data.episodes)
      setLoaded(true)
    }
  }, [query.data, setAvailableLabels, setAllEpisodeLabels, setLoaded])

  return query
}

/**
 * Hook to save labels for the current episode.
 */
export function useSaveEpisodeLabels() {
  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const commitEpisodeLabels = useLabelStore((state) => state.commitEpisodeLabels)
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: ({ episodeIdx, labels }: { episodeIdx: number; labels: string[] }) => {
      if (!currentDataset) throw new Error('No dataset selected')
      return setEpisodeLabels(currentDataset.id, episodeIdx, labels)
    },
    onSuccess: (data) => {
      commitEpisodeLabels(data.episode_index, data.labels)
      if (currentDataset) {
        queryClient.invalidateQueries({ queryKey: labelKeys.dataset(currentDataset.id) })
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
      return addLabelOption(currentDataset.id, label)
    },
    onSuccess: (data) => {
      setAvailableLabels(data)
      if (currentDataset) {
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
      return removeLabelOption(currentDataset.id, label)
    },
    onSuccess: (data, label) => {
      removeLabelOptionInStore(label)
      setAvailableLabels(data)
      if (currentDataset) {
        queryClient.invalidateQueries({ queryKey: labelKeys.dataset(currentDataset.id) })
      }
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
