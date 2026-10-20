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

  it('requests the principal under the document base path', async () => {
    const base = document.createElement('base')
    base.href = 'http://127.0.0.1:8001/proxy/'
    document.head.append(base)
    const mockFetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ scope_id: 'local', auth_mode: 'local' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', mockFetch)

    try {
      await fetchPrincipalContext()
    } finally {
      base.remove()
    }

    expect(mockFetch).toHaveBeenCalledWith('/proxy/api/auth/context', expect.any(Object))
  })
})
