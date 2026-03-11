import { InteractionStatus } from '@azure/msal-browser'
import { AuthenticatedTemplate, UnauthenticatedTemplate, useMsal } from '@azure/msal-react'
import { useEffect, useState } from 'react'

import { loginRequest } from '@/lib/auth-config'

/**
 * Checks if Azure Container Apps Easy Auth is handling authentication.
 * When Easy Auth is active, /.auth/me returns user claims and MSAL should not run.
 */
async function isEasyAuthActive(): Promise<boolean> {
  try {
    const response = await fetch('/.auth/me')
    if (!response.ok) return false
    const data = await response.json()
    return Array.isArray(data) && data.length > 0
  } catch {
    return false
  }
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [easyAuth, setEasyAuth] = useState<boolean | null>(null)

  useEffect(() => {
    isEasyAuthActive().then(setEasyAuth)
  }, [])

  if (easyAuth === null) return null
  if (easyAuth) return <>{children}</>

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

  useEffect(() => {
    if (inProgress === InteractionStatus.None) {
      instance.loginRedirect(loginRequest)
    }
  }, [instance, inProgress])

  return null
}
