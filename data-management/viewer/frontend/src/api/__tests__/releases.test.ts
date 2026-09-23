import { beforeEach, describe, expect, it } from 'vitest'

import {
  cancelRelease,
  evaluateRelease,
  getReleaseStatus,
  inspectRelease,
  submitRelease,
} from '@/api/releases'
import { _resetCsrfToken } from '@/lib/api-client'
import { installFetchMock, jsonResponse, mockFetch } from '@/test-utils/fetch-mocks'
import type { ReleaseSubmitRequest } from '@/types'

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

const response = {
  release_id: 'release-1',
  job_id: 'job-1',
  state: 'queued',
  eligible_episodes: [{ episode_index: 2, decision_id: 'decision-1', quality_run_id: 'quality-1' }],
  excluded_episodes: [],
  conflict: null,
  verification: { manifest_path: null, checksums_path: null, verified: false },
}

beforeEach(() => {
  _resetCsrfToken()
  installFetchMock()
})

describe('release API', () => {
  it('uses eligibility, submission, status, cancellation, and inspection routes', async () => {
    mockFetch.mockReset()
    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = input.toString()
      if (url === '/api/csrf-token') {
        return Promise.resolve(jsonResponse({ csrf_token: 'csrf-token' }))
      }
      if (url === '/api/releases/eligibility') {
        return Promise.resolve(
          jsonResponse({
            eligible_episodes: response.eligible_episodes,
            excluded_episodes: [
              { episode_index: 3, reason_codes: ['quality-required-check-failed'] },
            ],
          }),
        )
      }
      if (url === '/api/releases') {
        return Promise.resolve(jsonResponse(response, { status: 202 }))
      }
      if (url.endsWith('/cancel') && init?.method === 'POST') {
        return Promise.resolve(jsonResponse({ ...response, state: 'cancelled' }))
      }
      if (url === '/api/releases/jobs/job-1') {
        return Promise.resolve(jsonResponse({ ...response, state: 'running' }))
      }
      return Promise.resolve(
        jsonResponse({
          ...response,
          state: 'succeeded',
          verification: {
            manifest_path: 'releases/release-1/manifest.json',
            checksums_path: 'releases/release-1/checksums.sha256',
            verified: true,
          },
        }),
      )
    })

    const eligibility = await evaluateRelease(request)
    const submitted = await submitRelease(request)
    const status = await getReleaseStatus('job-1')
    const cancelled = await cancelRelease('job-1')
    const inspected = await inspectRelease('release-1')

    expect(eligibility.excludedEpisodes[0]).toEqual({
      episodeIndex: 3,
      reasonCodes: ['quality-required-check-failed'],
    })
    expect(submitted.jobId).toBe('job-1')
    expect(status.state).toBe('running')
    expect(cancelled.state).toBe('cancelled')
    expect(inspected.verification.verified).toBe(true)
    expect(mockFetch.mock.calls.slice(1).map(([url]) => url)).toEqual([
      '/api/releases/eligibility',
      '/api/releases',
      '/api/releases/jobs/job-1',
      '/api/releases/jobs/job-1/cancel',
      '/api/releases/release-1',
    ])
  })
})
