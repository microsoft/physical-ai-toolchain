import type { AccountInfo, AuthenticationResult, PublicClientApplication } from '@azure/msal-browser'
import { InteractionRequiredAuthError } from '@azure/msal-browser'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mockIsAuthEnabled = vi.hoisted(() => ({ value: false }))

vi.mock('../auth-config', () => ({
  get isAuthEnabled() {
    return mockIsAuthEnabled.value
  },
  loginRequest: { scopes: ['api://test-client-id/access_as_user'] },
}))

describe('auth-headers', () => {
  let getAuthHeaders: typeof import('../auth-headers').getAuthHeaders
  let setMsalInstance: typeof import('../auth-headers').setMsalInstance

  beforeEach(async () => {
    vi.resetModules()
    const mod = await import('../auth-headers')
    getAuthHeaders = mod.getAuthHeaders
    setMsalInstance = mod.setMsalInstance
  })

  afterEach(() => {
    mockIsAuthEnabled.value = false
  })

  it('returns empty headers when auth is disabled', async () => {
    mockIsAuthEnabled.value = false
    const headers = await getAuthHeaders()
    expect(headers).toEqual({})
  })

  it('returns empty headers when no MSAL instance is set', async () => {
    mockIsAuthEnabled.value = true
    const headers = await getAuthHeaders()
    expect(headers).toEqual({})
  })

  it('returns empty headers when no active account exists', async () => {
    mockIsAuthEnabled.value = true
    const mockInstance = {
      getActiveAccount: vi.fn().mockReturnValue(null),
    } as unknown as PublicClientApplication
    setMsalInstance(mockInstance)

    const headers = await getAuthHeaders()
    expect(headers).toEqual({})
  })

  it('returns Bearer token from silent acquisition', async () => {
    mockIsAuthEnabled.value = true
    const mockAccount = { homeAccountId: 'test' } as AccountInfo
    const mockInstance = {
      getActiveAccount: vi.fn().mockReturnValue(mockAccount),
      acquireTokenSilent: vi.fn().mockResolvedValue({
        accessToken: 'test-access-token-123',
      } as AuthenticationResult),
    } as unknown as PublicClientApplication
    setMsalInstance(mockInstance)

    const headers = await getAuthHeaders()
    expect(headers).toEqual({ Authorization: 'Bearer test-access-token-123' })
    expect(mockInstance.acquireTokenSilent).toHaveBeenCalledWith({
      scopes: ['api://test-client-id/access_as_user'],
      account: mockAccount,
    })
  })

  it('redirects and returns empty headers on InteractionRequiredAuthError', async () => {
    mockIsAuthEnabled.value = true
    const mockAccount = { homeAccountId: 'test' } as AccountInfo
    const mockInstance = {
      getActiveAccount: vi.fn().mockReturnValue(mockAccount),
      acquireTokenSilent: vi.fn().mockRejectedValue(
        new InteractionRequiredAuthError('interaction_required'),
      ),
      acquireTokenRedirect: vi.fn().mockResolvedValue(undefined),
    } as unknown as PublicClientApplication
    setMsalInstance(mockInstance)

    const headers = await getAuthHeaders()
    expect(headers).toEqual({})
    expect(mockInstance.acquireTokenRedirect).toHaveBeenCalledWith({
      scopes: ['api://test-client-id/access_as_user'],
    })
  })

  it('returns empty headers on unexpected errors', async () => {
    mockIsAuthEnabled.value = true
    const mockAccount = { homeAccountId: 'test' } as AccountInfo
    const mockInstance = {
      getActiveAccount: vi.fn().mockReturnValue(mockAccount),
      acquireTokenSilent: vi.fn().mockRejectedValue(new Error('network error')),
    } as unknown as PublicClientApplication
    setMsalInstance(mockInstance)

    const headers = await getAuthHeaders()
    expect(headers).toEqual({})
  })
})
