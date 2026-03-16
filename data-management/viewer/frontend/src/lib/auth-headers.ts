import {
  type AccountInfo,
  InteractionRequiredAuthError,
  PublicClientApplication,
} from '@azure/msal-browser'

import { isAuthEnabled, loginRequest } from './auth-config'

let msalInstance: PublicClientApplication | null = null

export function setMsalInstance(instance: PublicClientApplication): void {
  msalInstance = instance
}

export async function getAuthHeaders(): Promise<Record<string, string>> {
  if (!isAuthEnabled || !msalInstance) return {}

  const account: AccountInfo | null = msalInstance.getActiveAccount()
  if (!account) return {}

  try {
    const response = await msalInstance.acquireTokenSilent({
      ...loginRequest,
      account,
    })
    return { Authorization: `Bearer ${response.accessToken}` }
  } catch (error) {
    if (error instanceof InteractionRequiredAuthError) {
      await msalInstance.acquireTokenRedirect(loginRequest)
    }
    return {}
  }
}
