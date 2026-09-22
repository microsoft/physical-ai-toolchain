/**
 * TanStack Query hooks for annotation data fetching and mutations.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'

import {
  ApiClientError,
  deleteAnnotations,
  fetchAnnotations,
  fetchAnnotationSummary,
  saveAnnotation,
  triggerAutoAnalysis,
  type VersionedResource,
} from '@/lib/api-client'
import { loadPersistedAnnotationDraft, persistAnnotationDraft } from '@/lib/edit-draft-storage'
import { fetchPrincipalContext } from '@/lib/principal-context'
import { useAnnotationStore, useDatasetStore, useEpisodeStore } from '@/stores'
import type { EpisodeAnnotation, EpisodeAnnotationFile } from '@/types'

/**
 * Query key factory for annotations.
 */
export const annotationKeys = {
  all: ['annotations'] as const,
  lists: () => [...annotationKeys.all, 'list'] as const,
  list: (datasetId: string) => [...annotationKeys.lists(), datasetId] as const,
  details: () => [...annotationKeys.all, 'detail'] as const,
  resource: (datasetId: string, episodeIndex: number) =>
    [...annotationKeys.details(), datasetId, episodeIndex] as const,
  detail: (datasetId: string, episodeIndex: number, principalScopeId: string) =>
    [...annotationKeys.resource(datasetId, episodeIndex), principalScopeId] as const,
  summary: (datasetId: string) => [...annotationKeys.all, 'summary', datasetId] as const,
  autoAnalysis: (datasetId: string, episodeIndex: number) =>
    [...annotationKeys.all, 'auto', datasetId, episodeIndex] as const,
}

/**
 * Hook to fetch annotations for the current episode.
 *
 * Syncs the current user's annotation with the annotation store.
 *
 * @example
 * ```tsx
 * const { isLoading } = useEpisodeAnnotations();
 * const annotation = useAnnotationStore(state => state.currentAnnotation);
 * ```
 */
export function useEpisodeAnnotations() {
  const principalQuery = useQuery({
    queryKey: ['auth', 'principal-context'],
    queryFn: fetchPrincipalContext,
    staleTime: Number.POSITIVE_INFINITY,
  })
  const annotatorId = principalQuery.data?.scopeId
  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const currentIndex = useEpisodeStore((state) => state.currentIndex)
  const loadAnnotation = useAnnotationStore((state) => state.loadAnnotation)
  const initializeAnnotation = useAnnotationStore((state) => state.initializeAnnotation)
  const restoreAnnotationDraft = useAnnotationStore((state) => state.restoreAnnotationDraft)
  const currentAnnotation = useAnnotationStore((state) => state.currentAnnotation)
  const originalAnnotation = useAnnotationStore((state) => state.originalAnnotation)
  const isDirty = useAnnotationStore((state) => state.isDirty)
  const hydratedKeyRef = useRef<string | null>(null)

  const query = useQuery({
    queryKey: annotationKeys.detail(currentDataset?.id ?? '', currentIndex, annotatorId ?? ''),
    queryFn: () => fetchAnnotations(currentDataset!.id, currentIndex),
    enabled: !!currentDataset && currentIndex >= 0 && !!annotatorId,
    staleTime: 30 * 1000,
  })

  useEffect(() => {
    if (!query.data || !currentDataset || !annotatorId) return

    let active = true
    const key = `${currentDataset.id}:${currentIndex}:${annotatorId}`
    const userAnnotation = query.data.data.annotations.find(
      (annotation) => annotation.annotatorId === annotatorId,
    )

    if (!userAnnotation) {
      hydratedKeyRef.current = key
      initializeAnnotation(annotatorId)
      return
    }

    loadAnnotation(userAnnotation)
    hydratedKeyRef.current = key
    const hydrationEditGeneration = useAnnotationStore.getState().editGeneration
    void loadPersistedAnnotationDraft(currentDataset.id, currentIndex, annotatorId).then(
      (draft) => {
        if (!active) return
        const state = useAnnotationStore.getState()
        if (
          hydratedKeyRef.current !== key ||
          state.annotatorId !== annotatorId ||
          state.editGeneration !== hydrationEditGeneration
        ) {
          return
        }
        if (draft) restoreAnnotationDraft(draft.draft, userAnnotation)
      },
    )

    return () => {
      active = false
    }
  }, [
    annotatorId,
    currentDataset,
    currentIndex,
    initializeAnnotation,
    loadAnnotation,
    query.data,
    restoreAnnotationDraft,
  ])

  useEffect(() => {
    if (!currentDataset || !currentAnnotation || !annotatorId) return
    const key = `${currentDataset.id}:${currentIndex}:${annotatorId}`
    if (hydratedKeyRef.current !== key) return

    void persistAnnotationDraft(
      currentDataset.id,
      currentIndex,
      annotatorId,
      isDirty && originalAnnotation
        ? {
            draft: currentAnnotation,
            baseline: originalAnnotation,
            baseEtag: query.data?.etag ?? null,
          }
        : null,
    )
  }, [
    annotatorId,
    currentAnnotation,
    currentDataset,
    currentIndex,
    isDirty,
    originalAnnotation,
    query.data?.etag,
  ])

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
  const markSubmittedSaved = useAnnotationStore((state) => state.markSubmittedSaved)
  const setConflict = useAnnotationStore((state) => state.setConflict)

  return useMutation({
    mutationFn: ({
      datasetId,
      episodeIndex,
      annotation,
    }: {
      datasetId: string
      episodeIndex: number
      annotation: EpisodeAnnotation
    }) => {
      const queryKey = annotationKeys.detail(datasetId, episodeIndex, annotation.annotatorId)
      const current = queryClient.getQueryData<VersionedResource<EpisodeAnnotationFile>>(queryKey)
      const precondition = current?.etag ? { etag: current.etag } : { createOnly: true }
      return saveAnnotation(datasetId, episodeIndex, annotation, precondition)
    },

    onMutate: () => {
      setSaving(true)
      return { submittedEditGeneration: useAnnotationStore.getState().editGeneration }
    },

    onSuccess: (versioned, variables, context) => {
      markSubmittedSaved(variables.annotation, context.submittedEditGeneration)

      // Update cache
      queryClient.setQueryData(
        annotationKeys.detail(
          variables.datasetId,
          variables.episodeIndex,
          variables.annotation.annotatorId,
        ),
        versioned,
      )

      // Invalidate summary since it might have changed
      queryClient.invalidateQueries({
        queryKey: annotationKeys.summary(variables.datasetId),
      })
    },

    onError: (error, variables) => {
      if (error instanceof ApiClientError && error.status === 412) {
        const currentEtag =
          typeof error.details?.currentEtag === 'string' ? error.details.currentEtag : null
        setConflict(currentEtag, variables.annotation)
      }
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

  const save = (): Promise<VersionedResource<EpisodeAnnotationFile> | undefined> => {
    if (!currentDataset || currentIndex < 0 || !currentAnnotation) {
      return Promise.resolve(undefined)
    }

    return mutation.mutateAsync({
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
    mutationFn: ({ datasetId, episodeIndex }: { datasetId: string; episodeIndex: number }) => {
      const cached = queryClient.getQueriesData<VersionedResource<EpisodeAnnotationFile>>({
        queryKey: annotationKeys.resource(datasetId, episodeIndex),
      })
      const etag = cached.find(([, value]) => value?.etag)?.[1]?.etag
      if (!etag) throw new Error('Cannot delete annotations without a current revision')
      return deleteAnnotations(datasetId, episodeIndex, etag)
    },

    onSuccess: (_, variables) => {
      clear()

      // Invalidate queries
      queryClient.invalidateQueries({
        queryKey: annotationKeys.resource(variables.datasetId, variables.episodeIndex),
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
