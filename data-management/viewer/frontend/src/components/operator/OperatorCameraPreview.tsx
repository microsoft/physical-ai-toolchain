import { Video } from 'lucide-react'
import { useEffect, useState } from 'react'

import { fetchOperatorCameraFrame } from '@/api/operator'

interface OperatorCameraPreviewProps {
  active: boolean
  camera: string
}

export function OperatorCameraPreview({ active, camera }: OperatorCameraPreviewProps) {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const cameraName = `${camera.charAt(0).toUpperCase()}${camera.slice(1)}`

  useEffect(() => {
    if (!active) return
    let disposed = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let activeController: AbortController | undefined

    const refresh = async () => {
      activeController = new AbortController()
      try {
        const blob = await fetchOperatorCameraFrame(camera, activeController.signal)
        if (!disposed) {
          const nextUrl = URL.createObjectURL(blob)
          setPreviewUrl((current) => {
            if (current) URL.revokeObjectURL(current)
            return nextUrl
          })
        }
      } catch (error) {
        if (!(error instanceof DOMException && error.name === 'AbortError')) {
          // The next bounded poll retries frames that are not available yet.
        }
      }
      if (!disposed) timer = setTimeout(() => void refresh(), 200)
    }

    void refresh()
    return () => {
      disposed = true
      activeController?.abort()
      if (timer) clearTimeout(timer)
    }
  }, [active, camera])

  useEffect(
    () => () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl)
    },
    [previewUrl],
  )

  return (
    <section className="min-w-0 space-y-2">
      <h3 className="text-sm font-semibold">{cameraName} camera</h3>
      <div
        aria-label={`${camera} camera preview`}
        className="bg-muted/30 aspect-4/3 w-full overflow-hidden border"
      >
        {active && previewUrl ? (
          <img
            src={previewUrl}
            alt={`${camera} camera live preview`}
            className="h-full w-full object-contain"
          />
        ) : (
          <div className="text-muted-foreground flex h-full items-center justify-center gap-2 text-sm capitalize">
            <Video className="h-4 w-4" />
            {active ? `Waiting for ${camera} camera` : `${camera} camera stopped`}
          </div>
        )}
      </div>
    </section>
  )
}
