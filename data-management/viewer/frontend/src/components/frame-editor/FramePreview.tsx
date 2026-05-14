/**
 * Frame preview with applied transforms.
 *
 * Uses Canvas API to render the frame with crop and resize
 * transformations applied in real-time. Color adjustments are
 * previewed using CSS filters for performance.
 */

import { useEffect, useRef, useState } from 'react'

import { buildCssFilter } from '@/lib/css-filters'
import { cn } from '@/lib/utils'
import type { ImageTransform } from '@/types/episode-edit'

interface FramePreviewProps {
  /** Source frame URL */
  frameUrl: string
  /** Transform to apply */
  transform?: ImageTransform | null
  /** Maximum display width */
  maxWidth?: number
  /** Maximum display height */
  maxHeight?: number
  /** Additional CSS classes */
  className?: string
  /** Show original dimensions overlay */
  showDimensions?: boolean
}

/**
 * Canvas-based frame preview with transforms applied.
 *
 * @example
 * ```tsx
 * <FramePreview
 *   frameUrl="/api/frames/0"
 *   transform={{ crop: { x: 10, y: 10, width: 200, height: 150 } }}
 *   maxWidth={400}
 * />
 * ```
 */
export function FramePreview({
  frameUrl,
  transform,
  maxWidth = 400,
  maxHeight = 300,
  className,
  showDimensions = true,
}: FramePreviewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [dimensions, setDimensions] = useState<{
    original: { width: number; height: number }
    output: { width: number; height: number }
  } | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const img = new Image()
    img.crossOrigin = 'anonymous'

    img.onload = () => {
      setIsLoading(false)
      setError(null)

      const originalWidth = img.naturalWidth
      const originalHeight = img.naturalHeight

      // Calculate source region (crop or full image)
      let sx = 0
      let sy = 0
      let sw = originalWidth
      let sh = originalHeight

      if (transform?.crop) {
        sx = transform.crop.x
        sy = transform.crop.y
        sw = transform.crop.width
        sh = transform.crop.height
      }

      // Calculate output dimensions
      let outputWidth = sw
      let outputHeight = sh

      if (transform?.resize) {
        outputWidth = transform.resize.width
        outputHeight = transform.resize.height
      }

      // Scale to fit container while maintaining aspect ratio
      const scale = Math.min(
        maxWidth / outputWidth,
        maxHeight / outputHeight,
        1, // Don't upscale
      )

      const displayWidth = Math.round(outputWidth * scale)
      const displayHeight = Math.round(outputHeight * scale)

      canvas.width = displayWidth
      canvas.height = displayHeight

      // Draw the transformed image
      ctx.drawImage(img, sx, sy, sw, sh, 0, 0, displayWidth, displayHeight)

      setDimensions({
        original: { width: originalWidth, height: originalHeight },
        output: { width: outputWidth, height: outputHeight },
      })
    }

    img.onerror = () => {
      setIsLoading(false)
      setError('Failed to load frame')
    }

    setIsLoading(true)
    img.src = frameUrl

    return () => {
      img.onload = null
      img.onerror = null
    }
  }, [frameUrl, transform, maxWidth, maxHeight])

  // Generate CSS filter for color preview
  const cssFilter = buildCssFilter(transform?.colorAdjustment, transform?.colorFilter)

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <div className="bg-muted relative flex min-h-[100px] items-center justify-center overflow-hidden rounded-lg">
        {isLoading && (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="text-muted-foreground animate-pulse">Loading...</div>
          </div>
        )}

        {error && (
          <div className="text-destructive absolute inset-0 flex items-center justify-center">
            {error}
          </div>
        )}

        <canvas
          ref={canvasRef}
          style={{ filter: cssFilter || undefined }}
          className={cn('max-w-full', isLoading && 'opacity-0', error && 'hidden')}
        />
      </div>

      {showDimensions && dimensions && (
        <div className="text-muted-foreground flex flex-wrap gap-2 text-xs">
          <span>
            Original: {dimensions.original.width} × {dimensions.original.height}
          </span>
          <span>
            Output: {dimensions.output.width} × {dimensions.output.height}
          </span>
          {transform?.crop && <span className="text-blue-500">Cropped</span>}
          {transform?.resize && <span className="text-green-500">Resized</span>}
          {(transform?.colorAdjustment || transform?.colorFilter) && (
            <span className="text-purple-500">Color adjusted</span>
          )}
        </div>
      )}
    </div>
  )
}
