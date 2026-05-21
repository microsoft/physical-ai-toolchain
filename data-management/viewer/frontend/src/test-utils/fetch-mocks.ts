import { vi } from 'vitest'

import { TEST_CSRF_TOKEN } from './constants'

export const mockFetch = vi.fn()

export type JsonResponseLike = Response

export function jsonResponse(body: unknown, init?: number | ResponseInit): Response {
  const responseInit: ResponseInit =
    typeof init === 'number' ? { status: init } : { status: 200, ...(init ?? {}) }
  const headers = new Headers(responseInit.headers)
  if (!headers.has('content-type')) {
    headers.set('content-type', 'application/json')
  }
  return new Response(JSON.stringify(body), { ...responseInit, headers })
}

/** Queues a CSRF token fetch followed by the given mutation API response; prefer `installFetchMock({ csrf: true })`. */
export function mockMutationFetch(apiResponse: Response): void {
  mockFetch
    .mockResolvedValueOnce(jsonResponse({ csrf_token: TEST_CSRF_TOKEN }))
    .mockResolvedValueOnce(apiResponse)
}

/** Resets the shared mockFetch, stubs `globalThis.fetch`, and (by default) queues a CSRF token response so callers don't need `mockMutationFetch`. */
export function installFetchMock(options: { csrf?: boolean } = {}): typeof mockFetch {
  const { csrf = true } = options
  mockFetch.mockReset()
  vi.stubGlobal('fetch', mockFetch)
  if (csrf) {
    mockFetch.mockResolvedValueOnce(jsonResponse({ csrf_token: TEST_CSRF_TOKEN }))
  }
  return mockFetch
}
