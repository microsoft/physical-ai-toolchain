import { broadcastResponseToMainFrame } from '@azure/msal-browser/redirect-bridge'

void broadcastResponseToMainFrame().catch((error: unknown) => {
  const errorCode =
    typeof error === 'object' &&
    error !== null &&
    'errorCode' in error &&
    typeof error.errorCode === 'string' &&
    /^[a-z_]{1,64}$/.test(error.errorCode)
      ? error.errorCode
      : 'redirect_bridge_failed'
  // eslint-disable-next-line no-console
  console.error('Authentication redirect failed', { errorCode })
  document.getElementById('status')!.textContent =
    'Authentication could not complete. Return to the application and try signing in again.'
})
