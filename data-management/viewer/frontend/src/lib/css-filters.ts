/**
 * CSS filter string generation from color adjustment parameters.
 *
 * Shared between viewer display settings and frame edit preview.
 */

import type { ColorAdjustment, ColorFilterPreset } from '@/types/episode-edit'

/**
 * Generate a CSS `filter` value from adjustment parameters and an optional preset.
 *
 * Mapping:
 *  - brightness/contrast/saturation: CSS uses 1 = normal, so we add 1 to the
 *    -1…+1 range stored in ColorAdjustment.
 *  - gamma: approximated via brightness — CSS has no native gamma filter.
 *  - hue: mapped directly to hue-rotate().
 */
export function buildCssFilter(
  colorAdjustment?: ColorAdjustment,
  colorFilter?: ColorFilterPreset,
): string {
  const filters: string[] = []

  if (colorAdjustment) {
    if (colorAdjustment.brightness && colorAdjustment.brightness !== 0) {
      filters.push(`brightness(${1 + colorAdjustment.brightness})`)
    }
    if (colorAdjustment.contrast && colorAdjustment.contrast !== 0) {
      filters.push(`contrast(${1 + colorAdjustment.contrast})`)
    }
    if (colorAdjustment.saturation && colorAdjustment.saturation !== 0) {
      filters.push(`saturate(${1 + colorAdjustment.saturation})`)
    }
    if (colorAdjustment.hue && colorAdjustment.hue !== 0) {
      filters.push(`hue-rotate(${colorAdjustment.hue}deg)`)
    }
    if (colorAdjustment.gamma && colorAdjustment.gamma !== 1) {
      // gamma < 1 brightens shadows, gamma > 1 darkens them
      // CSS has no gamma filter; approximate by adjusting midtone brightness.
      // brightness(1/gamma) lifts shadows when gamma < 1.
      filters.push(`brightness(${(1 / colorAdjustment.gamma).toFixed(2)})`)
    }
  }

  if (colorFilter && colorFilter !== 'none') {
    switch (colorFilter) {
      case 'grayscale':
        filters.push('grayscale(1)')
        break
      case 'sepia':
        filters.push('sepia(1)')
        break
      case 'invert':
        filters.push('invert(1)')
        break
      case 'warm':
        filters.push('sepia(0.3) saturate(1.2)')
        break
      case 'cool':
        filters.push('hue-rotate(180deg) saturate(0.7)')
        break
    }
  }

  return filters.join(' ')
}

/**
 * Combine display-only viewer adjustments with edit-store transform filters.
 *
 * Returns a single CSS filter string, or undefined when both sources are inactive.
 */
export function combineCssFilters(
  displayAdjustment?: ColorAdjustment,
  displayActive?: boolean,
  editAdjustment?: ColorAdjustment,
  editFilter?: ColorFilterPreset,
): string | undefined {
  const parts: string[] = []

  if (displayActive && displayAdjustment) {
    const display = buildCssFilter(displayAdjustment)
    if (display) parts.push(display)
  }

  const edit = buildCssFilter(editAdjustment, editFilter)
  if (edit) parts.push(edit)

  return parts.length > 0 ? parts.join(' ') : undefined
}
