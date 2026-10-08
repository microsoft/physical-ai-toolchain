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

import { usePrincipalContext } from '@/hooks/use-principal-context'
import { fetchVlmJudgeSnapshot, fetchVlmJudgeStatus, runVlmJudge } from '@/lib/api-client'
import { assertEpisodeReadiness, assertSnapshotCurrent } from '@/lib/episode-readiness'
import type { PrincipalContext } from '@/lib/principal-context'
import { useDatasetStore } from '@/stores/dataset-store'
import { useEpisodeStore } from '@/stores/episode-store'
import type { VlmJudgeResult, VlmJudgeRunOptions, VlmJudgeStatus } from '@/types'

export const vlmJudgeKeys = {
  all: ['vlm-judge'] as const,
  episode: (datasetId: string, episodeIndex: number, principalScopeId: string) =>
    [...vlmJudgeKeys.all, datasetId, episodeIndex, principalScopeId] as const,
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
    },
  })
}
