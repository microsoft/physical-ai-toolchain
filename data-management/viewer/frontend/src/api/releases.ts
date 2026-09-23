import { apiRequest } from '@/lib/api-client'
import type { ReleaseEligibility, ReleaseSubmitRequest, ReleaseWorkflowResponse } from '@/types'

function releaseRequest<T>(path: string, method: 'GET' | 'POST', body?: unknown): Promise<T> {
  return apiRequest<T>(path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export function evaluateRelease(request: ReleaseSubmitRequest): Promise<ReleaseEligibility> {
  return releaseRequest('/releases/eligibility', 'POST', request)
}

export function submitRelease(request: ReleaseSubmitRequest): Promise<ReleaseWorkflowResponse> {
  return releaseRequest('/releases', 'POST', request)
}

export function getReleaseStatus(jobId: string): Promise<ReleaseWorkflowResponse> {
  return releaseRequest(`/releases/jobs/${encodeURIComponent(jobId)}`, 'GET')
}

export function listReleaseStatuses(datasetId: string): Promise<ReleaseWorkflowResponse[]> {
  return releaseRequest(`/releases/jobs?datasetId=${encodeURIComponent(datasetId)}`, 'GET')
}

export function cancelRelease(jobId: string): Promise<ReleaseWorkflowResponse> {
  return releaseRequest(`/releases/jobs/${encodeURIComponent(jobId)}/cancel`, 'POST')
}

export function inspectRelease(releaseId: string): Promise<ReleaseWorkflowResponse> {
  return releaseRequest(`/releases/${encodeURIComponent(releaseId)}`, 'GET')
}
