import { handleResponse, requestHeaders, transformKeys } from './api-client'

export type AuthMode = 'azure_ad' | 'auth0' | 'easy_auth' | 'apikey' | 'local'

export interface PrincipalContext {
  scopeId: string
  authMode: AuthMode
}

export async function fetchPrincipalContext(): Promise<PrincipalContext> {
  const response = await fetch('/api/auth/context', {
    headers: await requestHeaders(),
  })
  const data = await handleResponse<unknown>(response)
  return transformKeys<PrincipalContext>(data)
}
