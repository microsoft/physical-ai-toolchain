import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthGate } from '../AuthGate'

const mockLoginRedirect = vi.fn()

vi.mock('@azure/msal-react', () => ({
  AuthenticatedTemplate: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="authenticated">{children}</div>
  ),
  UnauthenticatedTemplate: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="unauthenticated">{children}</div>
  ),
  useMsal: () => ({
    instance: { loginRedirect: mockLoginRedirect },
    inProgress: 'none',
  }),
}))

vi.mock('@/lib/auth-config', () => ({
  loginRequest: { scopes: ['api://test-client-id/access_as_user'] },
}))

describe('AuthGate', () => {
  beforeEach(() => {
    mockLoginRedirect.mockClear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders children directly when Easy Auth is active', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve([{ userId: 'user-123' }]),
    } as Response)

    render(
      <AuthGate>
        <div>Protected Content</div>
      </AuthGate>,
    )

    await waitFor(() => {
      expect(screen.getByText('Protected Content')).toBeInTheDocument()
    })
    expect(mockLoginRedirect).not.toHaveBeenCalled()
  })

  it('falls back to MSAL when Easy Auth is not available', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      status: 404,
    } as Response)

    render(
      <AuthGate>
        <div>Protected Content</div>
      </AuthGate>,
    )

    await waitFor(() => {
      expect(screen.getByTestId('authenticated')).toBeInTheDocument()
    })
    expect(mockLoginRedirect).toHaveBeenCalledWith({
      scopes: ['api://test-client-id/access_as_user'],
    })
  })

  it('falls back to MSAL when Easy Auth fetch fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new Error('network error'))

    render(
      <AuthGate>
        <div>Protected Content</div>
      </AuthGate>,
    )

    await waitFor(() => {
      expect(screen.getByTestId('authenticated')).toBeInTheDocument()
    })
    expect(mockLoginRedirect).toHaveBeenCalled()
  })
})
