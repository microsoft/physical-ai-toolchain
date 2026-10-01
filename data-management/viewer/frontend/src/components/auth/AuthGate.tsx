import { InteractionStatus } from '@azure/msal-browser'
import { AuthenticatedTemplate, UnauthenticatedTemplate, useMsal } from '@azure/msal-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { loginRequest } from '@/lib/auth-config'

export function AuthGate({ children }: { children: React.ReactNode }) {
  return (
    <>
      <AuthenticatedTemplate>{children}</AuthenticatedTemplate>
      <UnauthenticatedTemplate>
        <LoginRedirect />
      </UnauthenticatedTemplate>
    </>
  )
}

function LoginRedirect() {
  const { instance, inProgress } = useMsal()
  const [redirectState, setRedirectState] = useState<'idle' | 'redirecting' | 'failed'>('idle')
  const automaticAttempted = useRef(false)
  const retryButtonRef = useRef<HTMLButtonElement>(null)

  const startLogin = useCallback(() => {
    automaticAttempted.current = true
    setRedirectState('redirecting')
    void instance.loginRedirect(loginRequest).catch(() => {
      setRedirectState('failed')
    })
  }, [instance])

  useEffect(() => {
    if (inProgress === InteractionStatus.None && !automaticAttempted.current) {
      startLogin()
    }
  }, [inProgress, startLogin])

  useEffect(() => {
    if (redirectState === 'failed') {
      retryButtonRef.current?.focus()
    }
  }, [redirectState])

  return (
    <div>
      <p role="status" aria-live="polite">
        {redirectState === 'redirecting' && 'Redirecting to Microsoft sign-in…'}
        {redirectState === 'failed' && 'Sign-in redirect stopped.'}
      </p>
      {redirectState === 'failed' && (
        <div role="alert">
          <p>Microsoft sign-in could not be started.</p>
          <button ref={retryButtonRef} type="button" onClick={startLogin}>
            Try sign-in again
          </button>
        </div>
      )}
    </div>
  )
}
