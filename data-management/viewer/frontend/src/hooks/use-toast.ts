/**
 * Toast notification hook.
 * Provides a simple interface for showing toast notifications.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

export interface Toast {
  id: string
  title?: string
  description?: string
  variant?: 'default' | 'destructive'
}

interface ToastOptions {
  title?: string
  description?: string
  variant?: 'default' | 'destructive'
}

export function useToast() {
  const [toasts, setToasts] = useState<Toast[]>([])
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())

  useEffect(() => {
    const timers = timersRef.current

    return () => {
      timers.forEach((timer) => clearTimeout(timer))
      timers.clear()
    }
  }, [])

  const toast = useCallback((options: ToastOptions) => {
    const id = Math.random().toString(36).slice(2)
    const newToast: Toast = { id, ...options }
    setToasts((prev) => [...prev, newToast])

    // Auto-dismiss after 5 seconds
    const timer = setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
      timersRef.current.delete(id)
    }, 5000)
    timersRef.current.set(id, timer)

    return id
  }, [])

  const dismiss = useCallback((id: string) => {
    const timer = timersRef.current.get(id)
    if (timer !== undefined) {
      clearTimeout(timer)
      timersRef.current.delete(id)
    }
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  return { toast, toasts, dismiss }
}
