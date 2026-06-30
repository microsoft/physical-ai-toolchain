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

import { fetchVlmJudgeStatus, runVlmJudge } from '@/lib/api-client'
import type { VlmJudgeResult, VlmJudgeRunOptions, VlmJudgeStatus } from '@/types'

export const vlmJudgeKeys = {
  all: ['vlm-judge'] as const,
  episode: (datasetId: string, episodeIndex: number) =>
    [...vlmJudgeKeys.all, datasetId, episodeIndex] as const,
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
  const isReady = enabled && !!datasetId && episodeIndex !== null && episodeIndex >= 0
  return useQuery<VlmJudgeStatus>({
    queryKey:
      datasetId && episodeIndex !== null
        ? vlmJudgeKeys.episode(datasetId, episodeIndex)
        : [...vlmJudgeKeys.all, 'idle'],
    queryFn: () => fetchVlmJudgeStatus(datasetId as string, episodeIndex as number),
    enabled: isReady,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    refetchInterval: (query) => {
      const data = query.state.data
      return data?.jobStatus === 'pending' || data?.jobStatus === 'running' ? 1000 : false
    },
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
  return useMutation<VlmJudgeResult, Error, RunVlmJudgeArgs>({
    mutationFn: ({ datasetId, episodeIndex, options }) =>
      runVlmJudge(datasetId, episodeIndex, options),
    onSuccess: (result, { datasetId, episodeIndex }) => {
      const queryKey = vlmJudgeKeys.episode(datasetId, episodeIndex)
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
