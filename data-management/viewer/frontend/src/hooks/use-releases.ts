import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  cancelRelease,
  evaluateRelease,
  getReleaseStatus,
  listReleaseStatuses,
  submitRelease,
} from '@/api/releases'
import type { ReleaseSubmitRequest, ReleaseWorkflowResponse } from '@/types'

const TERMINAL_STATES = new Set(['succeeded', 'cancelled', 'failed', 'conflict'])

export const releaseKeys = {
  all: ['releases'] as const,
  eligibility: (request: ReleaseSubmitRequest | null) =>
    [...releaseKeys.all, 'eligibility', request] as const,
  job: (jobId: string) => [...releaseKeys.all, 'job', jobId] as const,
  jobs: (datasetId: string) => [...releaseKeys.all, 'dataset', datasetId] as const,
}

export function useReleaseEligibility(request: ReleaseSubmitRequest | null) {
  return useQuery({
    queryKey: releaseKeys.eligibility(request),
    queryFn: () => evaluateRelease(request as ReleaseSubmitRequest),
    enabled: request !== null,
    staleTime: 10_000,
  })
}

export function useSubmitRelease() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (request: ReleaseSubmitRequest) => submitRelease(request),
    onSuccess: (response: ReleaseWorkflowResponse) => {
      queryClient.setQueryDefaults(releaseKeys.job(response.jobId), { gcTime: 30 * 60 * 1000 })
      queryClient.setQueryData(releaseKeys.job(response.jobId), response)
      queryClient.invalidateQueries({ queryKey: releaseKeys.all })
    },
  })
}

export function useReleaseJob(jobId: string | null) {
  return useQuery({
    queryKey: releaseKeys.job(jobId ?? ''),
    queryFn: () => getReleaseStatus(jobId as string),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const state = query.state.data?.state
      return state && TERMINAL_STATES.has(state) ? false : 2_000
    },
  })
}

export function useReleaseJobs(datasetId: string | null) {
  return useQuery({
    queryKey: releaseKeys.jobs(datasetId ?? ''),
    queryFn: () => listReleaseStatuses(datasetId as string),
    enabled: Boolean(datasetId),
    refetchInterval: (query) => {
      const hasActiveJob = query.state.data?.some((job) => !TERMINAL_STATES.has(job.state))
      return hasActiveJob ? 2_000 : 30_000
    },
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
  })
}

export function useCancelRelease() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (jobId: string) => cancelRelease(jobId),
    onSuccess: (response: ReleaseWorkflowResponse) => {
      queryClient.setQueryDefaults(releaseKeys.job(response.jobId), { gcTime: 30 * 60 * 1000 })
      queryClient.setQueryData(releaseKeys.job(response.jobId), response)
      queryClient.invalidateQueries({ queryKey: releaseKeys.all })
    },
  })
}
