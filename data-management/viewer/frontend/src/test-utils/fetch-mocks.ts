import { vi } from 'vitest'

export const mockFetch = vi.fn()

export interface JsonResponseLike {
  ok: boolean
  status: number
  statusText: string
  json: () => Promise<unknown>
}

export function jsonResponse(data: unknown, status = 200): JsonResponseLike {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    json: () => Promise.resolve(data),
  }
}

/**
 * Queues a CSRF token fetch followed by the given mutation API response.
 * Use for hooks that perform a mutation requiring a CSRF token.
 */
export function mockMutationFetch(apiResponse: JsonResponseLike): void {
  mockFetch
    .mockResolvedValueOnce(jsonResponse({ csrf_token: 'test-csrf-token' }))
    .mockResolvedValueOnce(apiResponse)
}

/**
 * Resets the shared mockFetch and stubs `globalThis.fetch` with it.
 * Call from `beforeEach` in tests that exercise fetch.
 */
export function installFetchMock(): void {
  mockFetch.mockReset()
  vi.stubGlobal('fetch', mockFetch)
}
