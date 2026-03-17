/**
 * Transform controls for resize and reset operations.
 */

import { Check, Lock, Unlock } from 'lucide-react'
import { useCallback, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
import { useTransformState } from '@/stores'
import type { ResizeDimensions } from '@/types/episode-edit'

interface TransformControlsProps {
  /** Original image dimensions for aspect ratio calculation */
  originalDimensions?: { width: number; height: number }
  /** Camera name for per-camera transforms */
  cameraName?: string
  /** Additional CSS classes */
  className?: string
}

/**
 * Controls for resize and reset operations.
 *
 * @example
 * ```tsx
 * <TransformControls
 *   originalDimensions={{ width: 640, height: 480 }}
 *   cameraName="top"
 * />
 * ```
 */
export function TransformControls({
  originalDimensions,
  cameraName,
  className,
}: TransformControlsProps) {
  const { globalTransform, setGlobalTransform, setCameraTransform } = useTransformState()

  // Get current resize from store
  const currentResize = cameraName
    ? undefined // Would need to get from cameraTransforms
    : globalTransform?.resize

  const [width, setWidth] = useState<string>(currentResize?.width?.toString() ?? '')
  const [height, setHeight] = useState<string>(currentResize?.height?.toString() ?? '')
  const [maintainAspect, setMaintainAspect] = useState(true)

  // Calculate aspect ratio
  const aspectRatio = originalDimensions ? originalDimensions.width / originalDimensions.height : 1

  // Handle width change with aspect ratio lock
  const handleWidthChange = useCallback(
    (value: string) => {
      setWidth(value)
      if (maintainAspect && value) {
        const numWidth = parseInt(value, 10)
        if (!isNaN(numWidth)) {
          setHeight(Math.round(numWidth / aspectRatio).toString())
        }
      }
    },
    [maintainAspect, aspectRatio],
  )

  // Handle height change with aspect ratio lock
  const handleHeightChange = useCallback(
    (value: string) => {
      setHeight(value)
      if (maintainAspect && value) {
        const numHeight = parseInt(value, 10)
        if (!isNaN(numHeight)) {
          setWidth(Math.round(numHeight * aspectRatio).toString())
        }
      }
    },
    [maintainAspect, aspectRatio],
  )

  // Apply resize to store
  const handleApplyResize = useCallback(() => {
    const numWidth = parseInt(width, 10)
    const numHeight = parseInt(height, 10)

    if (isNaN(numWidth) || isNaN(numHeight) || numWidth <= 0 || numHeight <= 0) {
      return
    }

    const resize: ResizeDimensions = { width: numWidth, height: numHeight }

    if (cameraName) {
      setCameraTransform(cameraName, {
        resize,
      })
    } else {
      setGlobalTransform({
        ...globalTransform,
        resize,
      })
    }
  }, [width, height, cameraName, globalTransform, setGlobalTransform, setCameraTransform])

  // Reset resize
  const handleResetResize = useCallback(() => {
    setWidth('')
    setHeight('')

    if (cameraName) {
      setCameraTransform(cameraName, null)
    } else {
      setGlobalTransform(globalTransform?.crop ? { crop: globalTransform.crop } : null)
    }
  }, [cameraName, globalTransform, setGlobalTransform, setCameraTransform])

  // Set common presets
  const handlePreset = useCallback((presetWidth: number, presetHeight: number) => {
    setWidth(presetWidth.toString())
    setHeight(presetHeight.toString())
  }, [])

  const hasValidDimensions = width && height && !isNaN(parseInt(width)) && !isNaN(parseInt(height))

  return (
    <div className={cn('flex flex-col gap-4', className)}>
      {/* Resize controls */}
      <div className="space-y-3">
        <Label className="text-sm font-medium">Resize Output</Label>

        <div className="flex items-end gap-2">
          <div className="flex-1">
            <Label htmlFor="resize-width" className="text-xs text-muted-foreground">
              Width
            </Label>
            <Input
              id="resize-width"
              type="number"
              min={1}
              value={width}
              onChange={(e) => handleWidthChange(e.target.value)}
              placeholder={originalDimensions?.width.toString() ?? 'Width'}
              className="h-8"
            />
          </div>

          <button
            type="button"
            onClick={() => setMaintainAspect(!maintainAspect)}
            className="p-2 text-muted-foreground transition-colors hover:text-foreground"
            title={maintainAspect ? 'Unlock aspect ratio' : 'Lock aspect ratio'}
          >
            {maintainAspect ? <Lock className="h-4 w-4" /> : <Unlock className="h-4 w-4" />}
          </button>

          <div className="flex-1">
            <Label htmlFor="resize-height" className="text-xs text-muted-foreground">
              Height
            </Label>
            <Input
              id="resize-height"
              type="number"
              min={1}
              value={height}
              onChange={(e) => handleHeightChange(e.target.value)}
              placeholder={originalDimensions?.height.toString() ?? 'Height'}
              className="h-8"
            />
          </div>
        </div>

        {/* Presets */}
        <div className="flex flex-wrap gap-1">
          <span className="mr-2 text-xs text-muted-foreground">Presets:</span>
          <Button
            variant="outline"
            size="sm"
            className="h-6 px-2 text-xs"
            onClick={() => handlePreset(640, 480)}
          >
            640×480
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-6 px-2 text-xs"
            onClick={() => handlePreset(320, 240)}
          >
            320×240
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-6 px-2 text-xs"
            onClick={() => handlePreset(224, 224)}
          >
            224×224
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-6 px-2 text-xs"
            onClick={() => handlePreset(256, 256)}
          >
            256×256
          </Button>
        </div>
      </div>

      {/* Action buttons */}
      <div className="flex gap-2">
        <Button
          size="sm"
          onClick={handleApplyResize}
          disabled={!hasValidDimensions}
          className="flex-1"
        >
          <Check className="mr-1 h-4 w-4" />
          Apply Resize
        </Button>
        <Button variant="outline" size="sm" onClick={handleResetResize}>
          Reset Size
        </Button>
      </div>

      {/* Current transform info */}
      {globalTransform && (
        <div className="rounded bg-muted p-2 text-xs text-muted-foreground">
          <div className="mb-1 font-medium">Current Transform:</div>
          {globalTransform.crop && (
            <div>
              Crop: {globalTransform.crop.width}×{globalTransform.crop.height} at (
              {globalTransform.crop.x}, {globalTransform.crop.y})
            </div>
          )}
          {globalTransform.resize && (
            <div>
              Resize: {globalTransform.resize.width}×{globalTransform.resize.height}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
