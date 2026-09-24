import { Camera, RefreshCcw } from 'lucide-react'
import { useEffect, useState } from 'react'

import { fetchOperatorCameraFrame } from '@/api/operator'
import { Button } from '@/components/ui/button'

interface OperatorCameraPreviewProps {
  active: boolean
  camera: string
}

export function OperatorCameraPreview({ active, camera }: OperatorCameraPreviewProps) {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [error, setError] = useState(false)
  const [retryKey, setRetryKey] = useState(0)
  const label = `${camera.charAt(0).toUpperCase()}${camera.slice(1)}`

  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    let disposed = false
    let timer: ReturnType<typeof setTimeout> | undefined

    const refresh = async (): Promise<void> => {
      try {
        const frame = await fetchOperatorCameraFrame(camera, controller.signal)
        if (disposed) return
        const nextUrl = URL.createObjectURL(frame.blob)
        setPreviewUrl((current) => {
          if (current) URL.revokeObjectURL(current)
          return nextUrl
        })
        setError(false)
      } catch {
        if (!controller.signal.aborted) setError(true)
      }
      if (!disposed) timer = setTimeout(() => void refresh(), 250)
    }

    void refresh()
    return () => {
      disposed = true
      controller.abort()
      if (timer) clearTimeout(timer)
    }
  }, [active, camera, retryKey])

  useEffect(
    () => () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl)
    },
    [previewUrl],
  )

  return (
    <section className="min-w-0 space-y-2" aria-labelledby={`operator-camera-${camera}-heading`}>
      <h3 id={`operator-camera-${camera}-heading`} className="text-sm font-semibold">
        {label} preview
      </h3>
      <div className="bg-muted/30 flex aspect-4/3 w-full items-center justify-center overflow-hidden border">
        {active && previewUrl ? (
          <img
            src={previewUrl}
            alt={`${label} live preview`}
            className="h-full w-full object-contain"
          />
        ) : (
          <div className="text-muted-foreground flex flex-col items-center gap-2 px-4 text-center text-sm">
            <Camera className="h-5 w-5" aria-hidden="true" />
            <span>
              {error
                ? `${label} preview unavailable`
                : active
                  ? `Waiting for ${label.toLowerCase()} frames`
                  : 'Preview starts when the worker owns the camera'}
            </span>
            {error && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setRetryKey((key) => key + 1)}
              >
                <RefreshCcw className="h-4 w-4" aria-hidden="true" />
                Retry preview
              </Button>
            )}
          </div>
        )}
      </div>
    </section>
  )
}
