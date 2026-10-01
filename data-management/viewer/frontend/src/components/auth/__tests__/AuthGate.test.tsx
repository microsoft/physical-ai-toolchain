import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthGate } from '../AuthGate'

const mockLoginRedirect = vi.fn()
const mockInstance = { loginRedirect: mockLoginRedirect }
let mockInProgress = 'none'
let mockAuthenticated = false

vi.mock('@azure/msal-react', () => ({
  AuthenticatedTemplate: ({ children }: { children: React.ReactNode }) =>
    mockAuthenticated ? <div data-testid="authenticated">{children}</div> : null,
  UnauthenticatedTemplate: ({ children }: { children: React.ReactNode }) =>
    mockAuthenticated ? null : <div data-testid="unauthenticated">{children}</div>,
  useMsal: () => ({
    instance: mockInstance,
    inProgress: mockInProgress,
  }),
}))

vi.mock('@/lib/auth-config', () => ({
  loginRequest: { scopes: ['api://test-client-id/access_as_user'] },
}))

describe('AuthGate', () => {
  beforeEach(() => {
    mockLoginRedirect.mockReset()
    mockLoginRedirect.mockResolvedValue(undefined)
    mockInProgress = 'none'
    mockAuthenticated = false
  })

  it('renders authenticated content without probing Easy Auth', async () => {
    mockAuthenticated = true
    const fetchSpy = vi.spyOn(globalThis, 'fetch')

    render(
      <AuthGate>
        <div>Protected Content</div>
      </AuthGate>,
    )

    await waitFor(() => {
      expect(screen.getByText('Protected Content')).toBeInTheDocument()
    })
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(mockLoginRedirect).not.toHaveBeenCalled()
  })

  it('starts the MSAL login redirect when no account is authenticated', async () => {
    render(
      <StrictMode>
        <AuthGate>
          <div>Protected Content</div>
        </AuthGate>
      </StrictMode>,
    )

    await waitFor(() => {
      expect(mockLoginRedirect).toHaveBeenCalledWith({
        scopes: ['api://test-client-id/access_as_user'],
      })
    })
    expect(mockLoginRedirect).toHaveBeenCalledTimes(1)
  })

  it('waits for an active MSAL interaction before starting login', () => {
    mockInProgress = 'login'

    render(
      <AuthGate>
        <div>Protected Content</div>
      </AuthGate>,
    )

    expect(mockLoginRedirect).not.toHaveBeenCalled()
  })

  it('shows a recoverable error when the login redirect fails', async () => {
    const user = userEvent.setup()
    mockLoginRedirect
      .mockRejectedValueOnce(new Error('redirect failed'))
      .mockRejectedValueOnce(new Error('redirect failed again'))

    const { rerender } = render(
      <AuthGate>
        <div>Protected Content</div>
      </AuthGate>,
    )

    const alert = await screen.findByRole('alert')
    const retryButton = screen.getByRole('button', { name: 'Try sign-in again' })
    expect(alert).toHaveTextContent('Microsoft sign-in could not be started.')
    expect(retryButton).toHaveFocus()

    mockInProgress = 'login'
    rerender(
      <AuthGate>
        <div>Protected Content</div>
      </AuthGate>,
    )
    mockInProgress = 'none'
    rerender(
      <AuthGate>
        <div>Protected Content</div>
      </AuthGate>,
    )
    expect(mockLoginRedirect).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('alert')).toBeInTheDocument()

    await user.click(retryButton)
    await waitFor(() => {
      expect(mockLoginRedirect).toHaveBeenCalledTimes(2)
    })
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try sign-in again' })).toHaveFocus()
  })

  it('updates one persistent live region as redirect state changes', async () => {
    let rejectRedirect: (reason?: unknown) => void = () => undefined
    mockLoginRedirect.mockImplementationOnce(
      () =>
        new Promise<never>((_resolve, reject) => {
          rejectRedirect = reject
        }),
    )

    render(
      <AuthGate>
        <div>Protected Content</div>
      </AuthGate>,
    )

    const liveRegion = screen.getByRole('status')
    await waitFor(() => {
      expect(liveRegion).toHaveTextContent('Redirecting to Microsoft sign-in')
    })

    await act(async () => {
      rejectRedirect(new Error('redirect failed'))
    })

    expect(screen.getByRole('status')).toBe(liveRegion)
    expect(liveRegion).toHaveTextContent('Sign-in redirect stopped')
  })
})
