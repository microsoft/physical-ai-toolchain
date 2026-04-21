import { type Configuration, LogLevel } from '@azure/msal-browser'

export const isAuthEnabled = !!import.meta.env.VITE_AZURE_CLIENT_ID

// MSAL v5 redirect bridge: not required unless COOP headers are set on the hosting server.
// See: https://github.com/AzureAD/microsoft-authentication-library-for-js/blob/dev/lib/msal-browser/docs/redirect-bridge.md
export const msalConfig: Configuration = {
  auth: {
    clientId: import.meta.env.VITE_AZURE_CLIENT_ID || '',
    authority: `https://login.microsoftonline.com/${import.meta.env.VITE_AZURE_TENANT_ID || 'common'}`,
    redirectUri: window.location.origin,
    postLogoutRedirectUri: '/',
  },
  cache: {
    cacheLocation: 'sessionStorage',
  },
  system: {
    loggerOptions: {
      loggerCallback: (_level, message, containsPii) => {
        if (containsPii) return
        // eslint-disable-next-line no-console
        if (_level === LogLevel.Error) console.error(message)
      },
    },
  },
}

export const loginRequest = {
  scopes: [`api://${import.meta.env.VITE_AZURE_CLIENT_ID}/access_as_user`],
}
