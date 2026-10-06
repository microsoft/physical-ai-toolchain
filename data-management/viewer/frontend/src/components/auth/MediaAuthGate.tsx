import { useMsal } from '@azure/msal-react'
import { useEffect, useState } from 'react'

import { handleResponse, mutationFetch } from '@/lib/api-client'

export function MediaAuthGate({ children }: { children: React.ReactNode }) {
  const { instance } = useMsal()
  const accountId = instance.getActiveAccount()?.homeAccountId
  const [readyAccountId, setReadyAccountId] = useState<string>()
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined

    async function authorizeMedia() {
      try {
        const response = await mutationFetch('/api/auth/media-session', {
          method: 'POST',
          signal: controller.signal,
        })
        const session = await handleResponse<{ expiresIn: number }>(response)
        if (!Number.isFinite(session.expiresIn) || session.expiresIn <= 0) {
          throw new Error('Invalid native media session lifetime')
        }
        if (controller.signal.aborted) return
        setFailed(false)
        setReadyAccountId(accountId)
        timer = setTimeout(authorizeMedia, (session.expiresIn * 1000) / 2)
      } catch (error: unknown) {
        if (controller.signal.aborted) return
        // eslint-disable-next-line no-console
        console.error('Dataset media authentication failed', {
          errorCode: error instanceof Error ? error.name : 'media_session_failed',
        })
        setFailed(true)
      }
    }

    void authorizeMedia()
    return () => {
      controller.abort()
      clearTimeout(timer)
    }
  }, [accountId])

  if (failed) {
    return (
      <p role="alert">
        Dataset media authentication could not complete. Reload the application to retry.
      </p>
    )
  }
  if (!accountId || readyAccountId !== accountId) return null
  return <>{children}</>
}
