/**
 * Color adjustment controls for image processing.
 *
 * Provides sliders for brightness, contrast, saturation, gamma, and hue,
 * plus preset color filter buttons.
 */

import { Contrast, Droplets, Palette, RotateCcw, Sun, SunDim } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
import { useTransformState } from '@/stores'
import type { ColorAdjustment, ColorFilterPreset } from '@/types/episode-edit'

interface ColorAdjustmentControlsProps {
  /** Camera name for per-camera transforms */
  cameraName?: string
  /** Additional CSS classes */
  className?: string
}

/** Default color adjustment values */
const DEFAULT_ADJUSTMENT: Required<ColorAdjustment> = {
  brightness: 0,
  contrast: 0,
  saturation: 0,
  gamma: 1,
  hue: 0,
}

/** Available color filter presets */
const FILTER_PRESETS: { value: ColorFilterPreset; label: string }[] = [
  { value: 'none', label: 'None' },
  { value: 'grayscale', label: 'Grayscale' },
  { value: 'sepia', label: 'Sepia' },
  { value: 'invert', label: 'Invert' },
  { value: 'warm', label: 'Warm' },
  { value: 'cool', label: 'Cool' },
]

interface SliderControlProps {
  label: string
  value: number
  onChange: (value: number) => void
  min: number
  max: number
  step: number
  icon: React.ReactNode
  formatValue?: (value: number) => string
}

/** Individual slider control for an adjustment parameter */
function SliderControl({
  label,
  value,
  onChange,
  min,
  max,
  step,
  icon,
  formatValue = (v) => v.toString(),
}: SliderControlProps) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <Label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          {icon}
          {label}
        </Label>
        <span className="w-12 text-right font-mono text-xs text-muted-foreground">
          {formatValue(value)}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="h-2 w-full cursor-pointer appearance-none rounded-lg bg-muted [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:cursor-pointer [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-0 [&::-moz-range-thumb]:bg-primary [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-primary [&::-webkit-slider-thumb]:transition-all [&::-webkit-slider-thumb]:hover:scale-110"
      />
    </div>
  )
}

/**
 * Color adjustment controls for frame editing.
 *
 * @example
 * ```tsx
 * <ColorAdjustmentControls cameraName="top" />
 * ```
 */
export function ColorAdjustmentControls({ cameraName, className }: ColorAdjustmentControlsProps) {
  const { globalTransform, setGlobalTransform, setCameraTransform } = useTransformState()

  // Get current color settings from store
  const currentAdjustment = cameraName
    ? undefined // Would need to get from cameraTransforms
    : globalTransform?.colorAdjustment
  const currentFilter = cameraName ? undefined : globalTransform?.colorFilter

  // Local state for adjustments (with defaults merged in)
  const [adjustment, setAdjustment] = useState<Required<ColorAdjustment>>(() => ({
    ...DEFAULT_ADJUSTMENT,
    ...currentAdjustment,
  }))
  const [filter, setFilter] = useState<ColorFilterPreset>(currentFilter ?? 'none')

  // Sync local state with store when store changes externally
  useEffect(() => {
    if (currentAdjustment) {
      setAdjustment({ ...DEFAULT_ADJUSTMENT, ...currentAdjustment })
    }
  }, [currentAdjustment])

  useEffect(() => {
    setFilter(currentFilter ?? 'none')
  }, [currentFilter])

  // Push current local state to the store
  const applyToStore = useCallback(
    (adj: Required<ColorAdjustment>, flt: ColorFilterPreset) => {
      const colorAdjustment: ColorAdjustment = {}
      if (adj.brightness !== 0) colorAdjustment.brightness = adj.brightness
      if (adj.contrast !== 0) colorAdjustment.contrast = adj.contrast
      if (adj.saturation !== 0) colorAdjustment.saturation = adj.saturation
      if (adj.gamma !== 1) colorAdjustment.gamma = adj.gamma
      if (adj.hue !== 0) colorAdjustment.hue = adj.hue

      const hasColorAdj = Object.keys(colorAdjustment).length > 0
      const colorFilter = flt !== 'none' ? flt : undefined

      if (cameraName) {
        const hasAny = hasColorAdj || colorFilter
        setCameraTransform(
          cameraName,
          hasAny
            ? { colorAdjustment: hasColorAdj ? colorAdjustment : undefined, colorFilter }
            : null,
        )
      } else {
        // Preserve resize/crop if they exist, but clear color fields
        const hasResize = !!globalTransform?.resize
        const hasCrop = !!globalTransform?.crop
        const hasAny = hasColorAdj || colorFilter || hasResize || hasCrop

        setGlobalTransform(
          hasAny
            ? {
                ...(hasCrop ? { crop: globalTransform!.crop } : {}),
                ...(hasResize ? { resize: globalTransform!.resize } : {}),
                ...(hasColorAdj ? { colorAdjustment } : {}),
                ...(colorFilter ? { colorFilter } : {}),
              }
            : null,
        )
      }
    },
    [cameraName, globalTransform, setGlobalTransform, setCameraTransform],
  )

  // Update a single adjustment value and apply immediately
  const updateAdjustment = useCallback(
    (key: keyof ColorAdjustment, value: number) => {
      setAdjustment((prev) => {
        const next = { ...prev, [key]: value }
        applyToStore(next, filter)
        return next
      })
    },
    [applyToStore, filter],
  )

  // Update filter and apply immediately
  const handleFilterChange = useCallback(
    (newFilter: ColorFilterPreset) => {
      setFilter(newFilter)
      applyToStore(adjustment, newFilter)
    },
    [applyToStore, adjustment],
  )

  // Reset to defaults
  const handleReset = useCallback(() => {
    setAdjustment(DEFAULT_ADJUSTMENT)
    setFilter('none')
    applyToStore(DEFAULT_ADJUSTMENT, 'none')
  }, [applyToStore])

  // Check if any adjustments have been made
  const hasChanges =
    adjustment.brightness !== 0 ||
    adjustment.contrast !== 0 ||
    adjustment.saturation !== 0 ||
    adjustment.gamma !== 1 ||
    adjustment.hue !== 0 ||
    filter !== 'none'

  return (
    <div className={cn('flex flex-col gap-4', className)}>
      {/* Adjustment sliders */}
      <div className="space-y-4">
        <Label className="text-sm font-medium">Color Adjustments</Label>

        <div className="space-y-3">
          <SliderControl
            label="Brightness"
            value={adjustment.brightness}
            onChange={(v) => updateAdjustment('brightness', v)}
            min={-1}
            max={1}
            step={0.05}
            icon={<Sun className="h-3 w-3" />}
            formatValue={(v) => `${v > 0 ? '+' : ''}${Math.round(v * 100)}%`}
          />

          <SliderControl
            label="Contrast"
            value={adjustment.contrast}
            onChange={(v) => updateAdjustment('contrast', v)}
            min={-1}
            max={1}
            step={0.05}
            icon={<Contrast className="h-3 w-3" />}
            formatValue={(v) => `${v > 0 ? '+' : ''}${Math.round(v * 100)}%`}
          />

          <SliderControl
            label="Saturation"
            value={adjustment.saturation}
            onChange={(v) => updateAdjustment('saturation', v)}
            min={-1}
            max={1}
            step={0.05}
            icon={<Droplets className="h-3 w-3" />}
            formatValue={(v) => `${v > 0 ? '+' : ''}${Math.round(v * 100)}%`}
          />

          <SliderControl
            label="Gamma"
            value={adjustment.gamma}
            onChange={(v) => updateAdjustment('gamma', v)}
            min={0.1}
            max={3}
            step={0.1}
            icon={<SunDim className="h-3 w-3" />}
            formatValue={(v) => v.toFixed(1)}
          />

          <SliderControl
            label="Hue"
            value={adjustment.hue}
            onChange={(v) => updateAdjustment('hue', v)}
            min={-180}
            max={180}
            step={5}
            icon={<Palette className="h-3 w-3" />}
            formatValue={(v) => `${v > 0 ? '+' : ''}${Math.round(v)}°`}
          />
        </div>
      </div>

      {/* Filter presets */}
      <div className="space-y-2">
        <Label className="text-sm font-medium">Color Filters</Label>
        <div className="flex flex-wrap gap-1">
          {FILTER_PRESETS.map((preset) => (
            <Button
              key={preset.value}
              variant={filter === preset.value ? 'default' : 'outline'}
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => handleFilterChange(preset.value)}
            >
              {preset.label}
            </Button>
          ))}
        </div>
      </div>

      {/* Reset button */}
      <div className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={handleReset}
          disabled={!hasChanges}
          className="flex-1"
        >
          <RotateCcw className="mr-1 h-4 w-4" />
          Reset
        </Button>
      </div>

      {/* Current color info */}
      {(globalTransform?.colorAdjustment || globalTransform?.colorFilter) && (
        <div className="rounded bg-muted p-2 text-xs text-muted-foreground">
          <div className="mb-1 font-medium">Active Color Settings:</div>
          {globalTransform.colorAdjustment && (
            <div className="space-y-0.5">
              {globalTransform.colorAdjustment.brightness !== undefined && (
                <div>
                  Brightness: {Math.round(globalTransform.colorAdjustment.brightness * 100)}%
                </div>
              )}
              {globalTransform.colorAdjustment.contrast !== undefined && (
                <div>Contrast: {Math.round(globalTransform.colorAdjustment.contrast * 100)}%</div>
              )}
              {globalTransform.colorAdjustment.saturation !== undefined && (
                <div>
                  Saturation: {Math.round(globalTransform.colorAdjustment.saturation * 100)}%
                </div>
              )}
              {globalTransform.colorAdjustment.gamma !== undefined && (
                <div>Gamma: {globalTransform.colorAdjustment.gamma.toFixed(1)}</div>
              )}
              {globalTransform.colorAdjustment.hue !== undefined && (
                <div>Hue: {Math.round(globalTransform.colorAdjustment.hue)}°</div>
              )}
            </div>
          )}
          {globalTransform.colorFilter && globalTransform.colorFilter !== 'none' && (
            <div>Filter: {globalTransform.colorFilter}</div>
          )}
        </div>
      )}
    </div>
  )
}
