import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

import { ApiClientError, apiRequestVersioned, mutationPreconditionHeaders } from '@/lib/api-client'
import { recordDiagnosticEvent } from '@/lib/playback-diagnostics'
import { useEditStore } from '@/stores/edit-store'
import { buildEditOperations, buildEditStateFromOperations } from '@/stores/edit-store-helpers'
import type { EpisodeEditOperations, SavedEditBaseline } from '@/types/episode-edit'

interface EditApiState {
  source_id: string
  source_revision: string
  author_id: string
  saved: null | {
    schema_version: string
    dataset_id: string
    episode_index: number
    source_id: string
    source_revision: string
    author_id: string
    operations: EpisodeEditOperations
  }
}

export const episodeEditKeys = {
  detail: (datasetId: string, episodeIndex: number, principalScopeId: string) =>
    ['episode-edits', datasetId, episodeIndex, principalScopeId] as const,
}

function sameScope(first: SavedEditBaseline, second: SavedEditBaseline): boolean {
  return (
    first.sourceId === second.sourceId &&
    first.sourceRevision === second.sourceRevision &&
    first.principalScopeId === second.principalScopeId &&
    first.etag === second.etag &&
    first.operations.datasetId === second.operations.datasetId &&
    first.operations.episodeIndex === second.operations.episodeIndex
  )
}

async function requestEdits(
  datasetId: string,
  episodeIndex: number,
  principalScopeId: string,
  submitted?: SavedEditBaseline,
): Promise<SavedEditBaseline> {
  const scope = { datasetId: datasetId.replace(/[\r\n]/g, ''), episodeIndex }
  recordDiagnosticEvent('persistence', submitted ? 'edit-save-started' : 'edit-read-started', scope)
  try {
    const result = await apiRequestVersioned<EditApiState>(
      `/datasets/${encodeURIComponent(datasetId)}/episodes/${episodeIndex}/edits`,
      submitted
        ? {
            method: 'PUT',
            headers: {
              'Content-Type': 'application/json',
              ...mutationPreconditionHeaders(
                submitted.etag ? { etag: submitted.etag } : { createOnly: true },
              ),
            },
            body: JSON.stringify({
              source_id: submitted.sourceId,
              source_revision: submitted.sourceRevision,
              operations: submitted.operations,
            }),
          }
        : { cache: 'no-store' },
      (data) => data as EditApiState,
    )
    const { data, etag } = result
    if (
      typeof data.source_id !== 'string' ||
      !data.source_id ||
      typeof data.source_revision !== 'string' ||
      !data.source_revision ||
      data.author_id !== principalScopeId ||
      data.saved === undefined
    )
      throw new Error('Saved edit scope was not returned correctly.')
    if ((data.saved || submitted) && !etag) throw new Error('Saved edit revision was not returned.')
    if (
      submitted &&
      (!data.saved ||
        data.source_id !== submitted.sourceId ||
        data.source_revision !== submitted.sourceRevision)
    ) {
      throw new Error('Saved edit source changed during persistence.')
    }
    if (
      data.saved &&
      (data.saved.schema_version !== '1.0.0' ||
        data.saved.dataset_id !== datasetId ||
        data.saved.episode_index !== episodeIndex ||
        data.saved.source_id !== data.source_id ||
        data.saved.source_revision !== data.source_revision ||
        data.saved.author_id !== principalScopeId ||
        data.saved.operations.datasetId !== datasetId ||
        data.saved.operations.episodeIndex !== episodeIndex)
    ) {
      throw new Error('Saved edit descriptor scope does not match the selected episode.')
    }
    const operations = buildEditOperations({
      datasetId,
      episodeIndex,
      ...buildEditStateFromOperations(data.saved?.operations ?? { datasetId, episodeIndex }),
    })!
    recordDiagnosticEvent(
      'persistence',
      submitted ? 'edit-save-completed' : 'edit-read-completed',
      scope,
    )
    return {
      sourceId: data.source_id,
      sourceRevision: data.source_revision,
      principalScopeId,
      etag,
      operations,
    }
  } catch (error) {
    recordDiagnosticEvent('persistence', submitted ? 'edit-save-failed' : 'edit-read-failed', {
      ...scope,
      status: error instanceof ApiClientError ? error.status : null,
    })
    throw error
  }
}

export function useEpisodeEdits(
  datasetId: string | null,
  episodeIndex: number | null,
  principalScopeId: string | undefined,
) {
  const draftHydrated = useEditStore((state) => state.draftHydrated)
  const draftError = useEditStore((state) => state.draftError)
  const baseline = useEditStore((state) => state.serverBaseline)
  const query = useQuery({
    queryKey: episodeEditKeys.detail(datasetId ?? '', episodeIndex ?? -1, principalScopeId ?? ''),
    queryFn: () => requestEdits(datasetId!, episodeIndex!, principalScopeId!),
    enabled: !!datasetId && episodeIndex !== null && episodeIndex >= 0 && !!principalScopeId,
    staleTime: 0,
    gcTime: 0,
  })
  useEffect(() => {
    if (!datasetId || episodeIndex === null || !principalScopeId) return
    const current = useEditStore.getState()
    if (
      current.datasetId !== datasetId ||
      current.episodeIndex !== episodeIndex ||
      current.principalScopeId !== principalScopeId
    ) {
      current.initializeEdit(datasetId, episodeIndex, principalScopeId)
    }
  }, [datasetId, episodeIndex, principalScopeId])
  useEffect(() => {
    if (query.data && !query.isFetching && !query.isError && draftHydrated) {
      useEditStore.getState().hydrateSavedEdits(query.data)
    }
  }, [query.data, query.isFetching, query.isError, draftHydrated])
  const scopeMatches = !!baseline && !!query.data && sameScope(baseline, query.data)
  const persistenceError =
    draftError ??
    (query.error
      ? 'Saved edits could not be loaded.'
      : query.data && draftHydrated && !scopeMatches
        ? 'Recovered edits do not match the saved revision. Resolve the draft before saving.'
        : null)
  return {
    ...query,
    persistenceError,
    isReady: scopeMatches && draftHydrated && !persistenceError && !query.isFetching,
  }
}

export function useSaveEpisodeEdits() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (submitted: SavedEditBaseline) => {
      const current = useEditStore.getState()
      if (
        !current.serverBaseline ||
        !sameScope(current.serverBaseline, submitted) ||
        !current.draftHydrated ||
        current.draftError ||
        current.validationErrors.length > 0
      ) {
        throw new Error('Load and resolve the current edit revision before saving.')
      }
      return requestEdits(
        submitted.operations.datasetId,
        submitted.operations.episodeIndex,
        submitted.principalScopeId,
        structuredClone(submitted),
      )
    },
    onSuccess: (saved, submitted) => {
      useEditStore.getState().acknowledgeSave(submitted, saved)
      queryClient.setQueryData(
        episodeEditKeys.detail(
          saved.operations.datasetId,
          saved.operations.episodeIndex,
          saved.principalScopeId,
        ),
        saved,
      )
    },
  })
}
