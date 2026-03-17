/**
 * TanStack Query hooks for annotation data fetching and mutations.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

import {
  deleteAnnotations,
  fetchAnnotations,
  fetchAnnotationSummary,
  saveAnnotation,
  triggerAutoAnalysis,
} from '@/lib/api-client'
import { useAnnotationStore, useDatasetStore, useEpisodeStore } from '@/stores'
import type { EpisodeAnnotation } from '@/types'

/**
 * Query key factory for annotations.
 */
export const annotationKeys = {
  all: ['annotations'] as const,
  lists: () => [...annotationKeys.all, 'list'] as const,
  list: (datasetId: string) => [...annotationKeys.lists(), datasetId] as const,
  details: () => [...annotationKeys.all, 'detail'] as const,
  detail: (datasetId: string, episodeIndex: number) =>
    [...annotationKeys.details(), datasetId, episodeIndex] as const,
  summary: (datasetId: string) => [...annotationKeys.all, 'summary', datasetId] as const,
  autoAnalysis: (datasetId: string, episodeIndex: number) =>
    [...annotationKeys.all, 'auto', datasetId, episodeIndex] as const,
}

/**
 * Hook to fetch annotations for the current episode.
 *
 * Syncs the current user's annotation with the annotation store.
 *
 * @param annotatorId - Current user's annotator ID
 *
 * @example
 * ```tsx
 * const { isLoading } = useEpisodeAnnotations('user-123');
 * const annotation = useAnnotationStore(state => state.currentAnnotation);
 * ```
 */
export function useEpisodeAnnotations(annotatorId: string) {
  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const currentIndex = useEpisodeStore((state) => state.currentIndex)
  const loadAnnotation = useAnnotationStore((state) => state.loadAnnotation)
  const initializeAnnotation = useAnnotationStore((state) => state.initializeAnnotation)

  const query = useQuery({
    queryKey: annotationKeys.detail(currentDataset?.id ?? '', currentIndex),
    queryFn: () => fetchAnnotations(currentDataset!.id, currentIndex),
    enabled: !!currentDataset && currentIndex >= 0,
    staleTime: 30 * 1000,
  })

  // Sync with annotation store
  useEffect(() => {
    if (query.data) {
      // Find the current user's annotation or initialize a new one
      const userAnnotation = query.data.annotations.find((a) => a.annotatorId === annotatorId)

      if (userAnnotation) {
        loadAnnotation(userAnnotation)
      } else {
        initializeAnnotation(annotatorId)
      }
    }
  }, [query.data, annotatorId, loadAnnotation, initializeAnnotation])

  return query
}

/**
 * Hook for saving annotations with optimistic updates.
 *
 * @example
 * ```tsx
 * const { mutate: save, isPending } = useSaveAnnotation();
 *
 * // Save current annotation
 * save({ datasetId: 'my-dataset', episodeIndex: 5, annotation });
 * ```
 */
export function useSaveAnnotation() {
  const queryClient = useQueryClient()
  const setSaving = useAnnotationStore((state) => state.setSaving)
  const setError = useAnnotationStore((state) => state.setError)
  const markSaved = useAnnotationStore((state) => state.markSaved)

  return useMutation({
    mutationFn: ({
      datasetId,
      episodeIndex,
      annotation,
    }: {
      datasetId: string
      episodeIndex: number
      annotation: EpisodeAnnotation
    }) => saveAnnotation(datasetId, episodeIndex, annotation),

    onMutate: () => {
      setSaving(true)
    },

    onSuccess: (data, variables) => {
      markSaved()

      // Update cache
      queryClient.setQueryData(
        annotationKeys.detail(variables.datasetId, variables.episodeIndex),
        data,
      )

      // Invalidate summary since it might have changed
      queryClient.invalidateQueries({
        queryKey: annotationKeys.summary(variables.datasetId),
      })
    },

    onError: (error) => {
      setError(error.message)
    },
  })
}

/**
 * Hook to save the current annotation from the store.
 *
 * Convenience hook that pulls annotation from store.
 *
 * @example
 * ```tsx
 * const { save, isPending } = useSaveCurrentAnnotation();
 *
 * // Save with one call
 * save();
 * ```
 */
export function useSaveCurrentAnnotation() {
  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const currentIndex = useEpisodeStore((state) => state.currentIndex)
  const currentAnnotation = useAnnotationStore((state) => state.currentAnnotation)
  const mutation = useSaveAnnotation()

  const save = () => {
    if (!currentDataset || currentIndex < 0 || !currentAnnotation) {
      return
    }

    mutation.mutate({
      datasetId: currentDataset.id,
      episodeIndex: currentIndex,
      annotation: currentAnnotation,
    })
  }

  return {
    save,
    isPending: mutation.isPending,
    isSuccess: mutation.isSuccess,
    isError: mutation.isError,
    error: mutation.error,
  }
}

/**
 * Hook for deleting annotations.
 *
 * @example
 * ```tsx
 * const { mutate: deleteAnnotation } = useDeleteAnnotation();
 *
 * deleteAnnotation({ datasetId: 'my-dataset', episodeIndex: 5 });
 * ```
 */
export function useDeleteAnnotation() {
  const queryClient = useQueryClient()
  const clear = useAnnotationStore((state) => state.clear)

  return useMutation({
    mutationFn: ({
      datasetId,
      episodeIndex,
      annotatorId,
    }: {
      datasetId: string
      episodeIndex: number
      annotatorId?: string
    }) => deleteAnnotations(datasetId, episodeIndex, annotatorId),

    onSuccess: (_, variables) => {
      // Clear store if deleting all
      if (!variables.annotatorId) {
        clear()
      }

      // Invalidate queries
      queryClient.invalidateQueries({
        queryKey: annotationKeys.detail(variables.datasetId, variables.episodeIndex),
      })
      queryClient.invalidateQueries({
        queryKey: annotationKeys.summary(variables.datasetId),
      })
    },
  })
}

/**
 * Hook for triggering auto-analysis.
 *
 * @example
 * ```tsx
 * const { mutate: analyze, data: analysis } = useAutoAnalysis();
 *
 * analyze({ datasetId: 'my-dataset', episodeIndex: 5 });
 * ```
 */
export function useAutoAnalysis() {
  const updateTrajectoryQuality = useAnnotationStore((state) => state.updateTrajectoryQuality)

  return useMutation({
    mutationFn: ({ datasetId, episodeIndex }: { datasetId: string; episodeIndex: number }) =>
      triggerAutoAnalysis(datasetId, episodeIndex),

    onSuccess: (data) => {
      // Auto-apply suggested values to annotation store
      updateTrajectoryQuality({
        overallScore: data.suggestedRating,
        flags: data.flags,
      })
    },
  })
}

/**
 * Hook for triggering auto-analysis on the current episode.
 *
 * @example
 * ```tsx
 * const { analyze, isPending, analysis } = useCurrentEpisodeAutoAnalysis();
 *
 * analyze();
 * ```
 */
export function useCurrentEpisodeAutoAnalysis() {
  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const currentIndex = useEpisodeStore((state) => state.currentIndex)
  const mutation = useAutoAnalysis()

  const analyze = () => {
    if (!currentDataset || currentIndex < 0) {
      return
    }

    mutation.mutate({
      datasetId: currentDataset.id,
      episodeIndex: currentIndex,
    })
  }

  return {
    analyze,
    isPending: mutation.isPending,
    analysis: mutation.data,
    isSuccess: mutation.isSuccess,
    isError: mutation.isError,
    error: mutation.error,
  }
}

/**
 * Hook to fetch annotation summary for a dataset.
 *
 * @param datasetId - Dataset ID
 *
 * @example
 * ```tsx
 * const { data: summary } = useAnnotationSummary('my-dataset');
 * ```
 */
export function useAnnotationSummary(datasetId: string | undefined) {
  return useQuery({
    queryKey: annotationKeys.summary(datasetId ?? ''),
    queryFn: () => fetchAnnotationSummary(datasetId!),
    enabled: !!datasetId,
    staleTime: 1 * 60 * 1000,
  })
}
