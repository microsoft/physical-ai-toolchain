import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchPrincipalContext } from '../principal-context'

describe('fetchPrincipalContext', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns only the opaque server principal contract', async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          scope_id: 'principal-scope',
          auth_mode: 'easy_auth',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', mockFetch)

    const context = await fetchPrincipalContext()

    expect(context).toEqual({ scopeId: 'principal-scope', authMode: 'easy_auth' })
    expect(mockFetch).toHaveBeenCalledWith('/api/auth/context', expect.any(Object))
  })
})
