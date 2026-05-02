import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

describe('auth-config', () => {
  const originalLocation = window.location

  beforeEach(() => {
    vi.resetModules()
    Object.defineProperty(window, 'location', {
      configurable: true,
      writable: true,
      value: { ...originalLocation, origin: 'https://example.test' },
    })
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    vi.restoreAllMocks()
    Object.defineProperty(window, 'location', {
      configurable: true,
      writable: true,
      value: originalLocation,
    })
  })

  it('isAuthEnabled is false when VITE_AZURE_CLIENT_ID is empty', async () => {
    vi.stubEnv('VITE_AZURE_CLIENT_ID', '')
    const mod = await import('@/lib/auth-config')
    expect(mod.isAuthEnabled).toBe(false)
  })

  it('isAuthEnabled is true when VITE_AZURE_CLIENT_ID is set', async () => {
    vi.stubEnv('VITE_AZURE_CLIENT_ID', 'client-123')
    const mod = await import('@/lib/auth-config')
    expect(mod.isAuthEnabled).toBe(true)
  })

  it('uses common tenant by default', async () => {
    vi.stubEnv('VITE_AZURE_CLIENT_ID', 'client-123')
    vi.stubEnv('VITE_AZURE_TENANT_ID', '')
    const mod = await import('@/lib/auth-config')
    expect(mod.msalConfig.auth.authority).toBe('https://login.microsoftonline.com/common')
  })

  it('uses explicit tenant when provided', async () => {
    vi.stubEnv('VITE_AZURE_CLIENT_ID', 'client-123')
    vi.stubEnv('VITE_AZURE_TENANT_ID', 'tenant-xyz')
    const mod = await import('@/lib/auth-config')
    expect(mod.msalConfig.auth.authority).toBe('https://login.microsoftonline.com/tenant-xyz')
  })

  it('redirectUri matches window.location.origin and postLogoutRedirectUri is /', async () => {
    vi.stubEnv('VITE_AZURE_CLIENT_ID', 'client-123')
    const mod = await import('@/lib/auth-config')
    expect(mod.msalConfig.auth.redirectUri).toBe('https://example.test')
    expect(mod.msalConfig.auth.postLogoutRedirectUri).toBe('/')
  })

  it('cacheLocation is sessionStorage', async () => {
    vi.stubEnv('VITE_AZURE_CLIENT_ID', 'client-123')
    const mod = await import('@/lib/auth-config')
    expect(mod.msalConfig.cache?.cacheLocation).toBe('sessionStorage')
  })

  it('loginRequest scopes use the client id', async () => {
    vi.stubEnv('VITE_AZURE_CLIENT_ID', 'client-abc')
    const mod = await import('@/lib/auth-config')
    expect(mod.loginRequest.scopes).toEqual(['api://client-abc/access_as_user'])
  })

  it('loggerCallback skips messages flagged as containing PII', async () => {
    vi.stubEnv('VITE_AZURE_CLIENT_ID', 'client-123')
    const { msalConfig } = await import('@/lib/auth-config')
    const { LogLevel } = await import('@azure/msal-browser')
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    msalConfig.system?.loggerOptions?.loggerCallback?.(LogLevel.Error, 'secret', true)
    expect(errSpy).not.toHaveBeenCalled()
  })

  it('loggerCallback writes Error level messages to console.error', async () => {
    vi.stubEnv('VITE_AZURE_CLIENT_ID', 'client-123')
    const { msalConfig } = await import('@/lib/auth-config')
    const { LogLevel } = await import('@azure/msal-browser')
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    msalConfig.system?.loggerOptions?.loggerCallback?.(LogLevel.Error, 'boom', false)
    expect(errSpy).toHaveBeenCalledWith('boom')
  })

  it('loggerCallback ignores non-Error levels', async () => {
    vi.stubEnv('VITE_AZURE_CLIENT_ID', 'client-123')
    const { msalConfig } = await import('@/lib/auth-config')
    const { LogLevel } = await import('@azure/msal-browser')
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    msalConfig.system?.loggerOptions?.loggerCallback?.(LogLevel.Warning, 'warn', false)
    msalConfig.system?.loggerOptions?.loggerCallback?.(LogLevel.Info, 'info', false)
    msalConfig.system?.loggerOptions?.loggerCallback?.(LogLevel.Verbose, 'verbose', false)
    expect(errSpy).not.toHaveBeenCalled()
  })
})
