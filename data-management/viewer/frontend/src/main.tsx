import './index.css'

import { AccountInfo, EventType, PublicClientApplication } from '@azure/msal-browser'
import { MsalProvider } from '@azure/msal-react'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'
import { AuthGate } from './components/auth/AuthGate'
import { isAuthEnabled, msalConfig } from './lib/auth-config'
import { setMsalInstance } from './lib/auth-headers'

let msalInstance: PublicClientApplication | null = null

if (isAuthEnabled) {
  msalInstance = new PublicClientApplication(msalConfig)
  setMsalInstance(msalInstance)

  const accounts = msalInstance.getAllAccounts()
  if (!msalInstance.getActiveAccount() && accounts.length > 0) {
    msalInstance.setActiveAccount(accounts[0])
  }

  msalInstance.addEventCallback((event) => {
    if (event.eventType === EventType.LOGIN_SUCCESS && event.payload) {
      msalInstance!.setActiveAccount(event.payload as AccountInfo)
    }
  })
}

function Root() {
  if (isAuthEnabled && msalInstance) {
    return (
      <MsalProvider instance={msalInstance}>
        <AuthGate>
          <App />
        </AuthGate>
      </MsalProvider>
    )
  }
  return <App />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
