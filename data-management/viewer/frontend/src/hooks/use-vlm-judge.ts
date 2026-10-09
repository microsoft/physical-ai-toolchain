/**
 * TanStack Query hooks for the VLM-as-judge endpoints.
 *
 * - ``useVlmJudgeStatus`` — read-only fetch of any cached judgment for the
 *   currently-selected episode. Cheap, runs on every episode change.
 * - ``useRunVlmJudge`` — mutation that invokes the backend (cache-first
 *   unless ``force`` is set) and writes the result into the query cache so
 *   the panel renders immediately.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

import { usePrincipalContext } from '@/hooks/use-principal-context'
import {
  fetchJudgeEvidence,
  fetchJudgeJob,
  fetchVlmJudgeSnapshot,
  fetchVlmJudgeStatus,
  mutateJudgeDataset,
  runVlmJudge,
} from '@/lib/api-client'
import { assertEpisodeReadiness, assertSnapshotCurrent } from '@/lib/episode-readiness'
import { recordDiagnosticEvent } from '@/lib/playback-diagnostics'
import type { PrincipalContext } from '@/lib/principal-context'
import { useDatasetStore } from '@/stores/dataset-store'
import { useEpisodeStore } from '@/stores/episode-store'
import type { JudgeJob, VlmJudgeResult, VlmJudgeRunOptions, VlmJudgeStatus } from '@/types'

export const vlmJudgeKeys = {
  all: ['vlm-judge'] as const,
  episode: (datasetId: string, episodeIndex: number, principalScopeId: string) =>
    [...vlmJudgeKeys.all, datasetId, episodeIndex, principalScopeId] as const,
}

export function useJudgeEvidence(datasetId: string | null, episodeIndex: number, enabled = true) {
  const principal = usePrincipalContext()
  const source = useEpisodeStore((state) => state.currentEpisode)
  const activeDataset = useDatasetStore((state) => state.currentDataset?.id)
  const ready =
    enabled &&
    !!datasetId &&
    activeDataset === datasetId &&
    source?.meta.index === episodeIndex &&
    !!source.sourceId &&
    !!source.sourceRevision &&
    !!principal.data?.scopeId &&
    !principal.error
  const query = useQuery({
    queryKey: [
      'judge-evidence',
      datasetId,
      principal.data?.scopeId,
      source?.sourceId,
      source?.sourceRevision,
      episodeIndex,
    ],
    queryFn: () => fetchJudgeEvidence(datasetId!, episodeIndex),
    enabled: ready,
    retry: false,
  })
  const denied =
    !ready ||
    (query.error && 'status' in query.error && [401, 403, 404].includes(Number(query.error.status)))
  useEffect(() => {
    if (ready && query.error)
      recordDiagnosticEvent('workspace', 'evidence-refresh-error', {
        retained: !!query.data && !denied,
      })
  }, [ready, query.error, query.data, denied])
  const data =
    denied || !query.data
      ? undefined
      : {
          ...query.data,
          items: query.data.items.filter(
            (item) =>
              item.input.sourceId === source?.sourceId &&
              item.input.sourceRevision === source?.sourceRevision,
          ),
        }
  return {
    ...query,
    data,
    refetch: () => {
      recordDiagnosticEvent('workspace', 'evidence-refresh-retry')
      return query.refetch()
    },
  }
}

export function useApplyJudgeResult() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async ({
      datasetId,
      episodeIndex,
      runId,
    }: {
      datasetId: string
      episodeIndex: number
      runId: string
    }) => {
      const scope = () =>
        JSON.stringify([
          client.getQueryData<PrincipalContext>(['auth', 'principal-context'])?.scopeId,
          client.getQueryState(['auth', 'principal-context'])?.status,
          useDatasetStore.getState().currentDataset?.id,
          useEpisodeStore.getState().currentIndex,
          useEpisodeStore.getState().currentEpisode?.sourceRevision,
        ])
      const capturedScope = scope()
      await assertEpisodeReadiness(client, datasetId, [episodeIndex])
      if (scope() !== capturedScope)
        throw new Error('Application context changed. Refresh before applying.')
      const result = await mutateJudgeDataset(datasetId, {
        kind: 'apply',
        jobId: runId,
        indices: [episodeIndex],
      })
      if (scope() !== capturedScope)
        throw new Error('Application context changed. Refresh saved evidence.')
      // The worker writes labels after the 202; refreshing earlier leaves a stale label revision.
      let job = result as JudgeJob
      for (let attempt = 0; job.status === 'queued' && attempt < 60; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1000))
        job = await fetchJudgeJob(runId)
      }
      if (job.status === 'queued')
        throw new Error('the server has not applied it yet. Check the dataset job list.')
      if (job.applicationErrors) throw new Error('the server could not write the label.')
      return job
    },
    onSuccess: (_result, { datasetId }) => {
      void client.invalidateQueries({ queryKey: ['judge-dataset', datasetId] })
      void client.invalidateQueries({ queryKey: ['judge-evidence', datasetId] })
      void client.invalidateQueries({ queryKey: ['labels', datasetId] })
      recordDiagnosticEvent('workspace', 'judge-application-requested')
    },
    onError: () => recordDiagnosticEvent('workspace', 'judge-application-error'),
  })
}

interface UseVlmJudgeStatusOptions {
  datasetId: string | null
  episodeIndex: number | null
  enabled?: boolean
}

export function useVlmJudgeStatus({
  datasetId,
  episodeIndex,
  enabled = true,
}: UseVlmJudgeStatusOptions) {
  const principal = usePrincipalContext()
  const principalScopeId = principal.data?.scopeId
  const isReady =
    enabled &&
    !!principalScopeId &&
    !principal.error &&
    !!datasetId &&
    episodeIndex !== null &&
    episodeIndex >= 0
  return useQuery<VlmJudgeStatus>({
    queryKey:
      datasetId && episodeIndex !== null && principalScopeId
        ? vlmJudgeKeys.episode(datasetId, episodeIndex, principalScopeId)
        : [...vlmJudgeKeys.all, 'idle'],
    queryFn: () => fetchVlmJudgeStatus(datasetId as string, episodeIndex as number),
    enabled: isReady,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    retry: false,
  })
}

interface RunVlmJudgeArgs {
  datasetId: string
  episodeIndex: number
  options?: VlmJudgeRunOptions
}

export function useRunVlmJudge() {
  const queryClient = useQueryClient()
  return useMutation<VlmJudgeResult, Error, RunVlmJudgeArgs, { scopeId: string | undefined }>({
    onMutate: () => ({
      scopeId: queryClient.getQueryData<PrincipalContext>(['auth', 'principal-context'])?.scopeId,
    }),
    mutationFn: async ({ datasetId, episodeIndex, options }) => {
      const currentScope = () =>
        JSON.stringify([
          queryClient.getQueryData<PrincipalContext>(['auth', 'principal-context'])?.scopeId,
          queryClient.getQueryState(['auth', 'principal-context'])?.status,
          useDatasetStore.getState().currentDataset?.id,
          useEpisodeStore.getState().currentIndex,
          useEpisodeStore.getState().currentEpisode?.sourceId,
          useEpisodeStore.getState().currentEpisode?.sourceRevision,
        ])
      const scope = currentScope()
      await assertEpisodeReadiness(queryClient, datasetId, [episodeIndex])
      const snapshot = await fetchVlmJudgeSnapshot(
        datasetId,
        episodeIndex,
        options?.annotationAuthorId,
      )
      await assertEpisodeReadiness(queryClient, datasetId, [episodeIndex])
      assertSnapshotCurrent(queryClient, datasetId, episodeIndex, snapshot)
      if (currentScope() !== scope) throw new Error('Judge context changed. Reload before judging.')
      const result = await runVlmJudge(datasetId, episodeIndex, {
        ...options,
        snapshotId: snapshot.snapshotId,
      })
      if (currentScope() !== scope) throw new Error('Judge context changed. Reload before judging.')
      assertSnapshotCurrent(queryClient, datasetId, episodeIndex, snapshot)
      return result
    },
    onSuccess: (result, { datasetId, episodeIndex }, context) => {
      if (
        !context?.scopeId ||
        context.scopeId !==
          queryClient.getQueryData<PrincipalContext>(['auth', 'principal-context'])?.scopeId
      ) {
        throw new Error('Judge context changed. Reload before judging.')
      }
      const queryKey = vlmJudgeKeys.episode(datasetId, episodeIndex, context.scopeId)
      const existing = queryClient.getQueryData<VlmJudgeStatus>(queryKey)
      const status: VlmJudgeStatus = {
        ...existing,
        enabled: true,
        cached: result.cached,
        judgeModel: result.judgeModel,
        promptVersion: result.promptVersion,
        cacheKey: existing?.cacheKey ?? null,
        processMethod: result.processMethod ?? existing?.processMethod ?? null,
        nFrames: existing?.nFrames ?? result.nFrames,
        result,
      }
      queryClient.setQueryData(queryKey, status)
      void queryClient.invalidateQueries({
        queryKey: ['judge-evidence', datasetId, context.scopeId],
      })
      void queryClient.invalidateQueries({
        queryKey: ['judge-dataset', datasetId, context.scopeId],
      })
    },
  })
}
