import { act, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  releaseKeys,
  useCancelRelease,
  useReleaseEligibility,
  useReleaseJob,
  useSubmitRelease,
} from '@/hooks/use-releases'
import { createTestQueryClient, renderHookWithProviders } from '@/test-utils/render'
import type { ReleaseSubmitRequest, ReleaseWorkflowResponse } from '@/types'

const api = vi.hoisted(() => ({
  cancelRelease: vi.fn(),
  evaluateRelease: vi.fn(),
  getReleaseStatus: vi.fn(),
  submitRelease: vi.fn(),
}))

vi.mock('@/api/releases', () => api)

const request: ReleaseSubmitRequest = {
  releaseId: 'release-1',
  datasetId: 'dataset-1',
  actorId: 'reviewer',
  reason: 'Approved training set',
  destinationKind: 'local',
  idempotencyKey: 'request-1',
  targetFormat: { name: 'lerobot', version: '3.0' },
  episodes: [{ episodeIndex: 2, decisionId: 'decision-1' }],
}

const workflow: ReleaseWorkflowResponse = {
  releaseId: 'release-1',
  jobId: 'job-1',
  state: 'queued',
  eligibleEpisodes: [{ episodeIndex: 2, decisionId: 'decision-1', qualityRunId: 'quality-1' }],
  excludedEpisodes: [],
  rejectedEpisodes: [],
  eligibilityFingerprint: 'f'.repeat(64),
  conflict: null,
  verification: { manifestPath: null, checksumsPath: null, verified: false },
}

beforeEach(() => {
  vi.clearAllMocks()
  api.evaluateRelease.mockResolvedValue({
    eligibleEpisodes: workflow.eligibleEpisodes,
    excludedEpisodes: [],
  })
  api.submitRelease.mockResolvedValue(workflow)
  api.getReleaseStatus.mockResolvedValue({ ...workflow, state: 'running' })
  api.cancelRelease.mockResolvedValue({ ...workflow, state: 'cancelled' })
})

describe('release hooks', () => {
  it('preflights eligibility and stores a submitted durable job', async () => {
    const { result: eligibility } = renderHookWithProviders(() => useReleaseEligibility(request))

    await waitFor(() => expect(eligibility.current.isSuccess).toBe(true))

    const queryClient = createTestQueryClient()
    queryClient.setQueryDefaults(releaseKeys.all, { gcTime: Number.POSITIVE_INFINITY })
    const { result: submit } = renderHookWithProviders(() => useSubmitRelease(), { queryClient })
    act(() => submit.current.mutate(request))
    await waitFor(() => expect(submit.current.isSuccess).toBe(true))

    expect(queryClient.getQueryData(releaseKeys.job('job-1'))).toEqual(workflow)
  })

  it('polls and cancels the durable job through shared query state', async () => {
    const { result: job } = renderHookWithProviders(() => useReleaseJob('job-1'))
    await waitFor(() => expect(job.current.data?.state).toBe('running'))

    const { result: cancel } = renderHookWithProviders(() => useCancelRelease())
    act(() => cancel.current.mutate('job-1'))
    await waitFor(() => expect(cancel.current.data?.state).toBe('cancelled'))

    expect(api.getReleaseStatus).toHaveBeenCalledWith('job-1')
    expect(api.cancelRelease).toHaveBeenCalledWith('job-1')
  })
})
