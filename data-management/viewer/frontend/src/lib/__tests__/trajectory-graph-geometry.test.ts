import { describe, expect, it } from 'vitest'

import {
  resolveSelectionHighlightStyle,
  resolveSurfaceFrame,
  resolveTrajectoryPlotArea,
} from '../trajectory-graph-geometry'

describe('resolveTrajectoryPlotArea', () => {
  it('uses the chart clip rect instead of the full overlay width when available', () => {
    const surface = document.createElement('div')
    const container = document.createElement('div')

    container.innerHTML = `
      <svg>
        <defs>
          <clipPath id="plot-area">
            <rect x="60" y="5" width="682" height="208"></rect>
          </clipPath>
        </defs>
      </svg>
    `

    container.appendChild(surface)

    Object.defineProperty(surface, 'parentElement', {
      configurable: true,
      value: container,
    })

    Object.defineProperty(surface, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ left: 0, top: 0, right: 762, bottom: 248, width: 762, height: 248 }),
    })

    expect(resolveTrajectoryPlotArea(surface)).toEqual({ left: 60, width: 682 })
  })

  it('falls back to the full overlay width when no plot clip rect exists', () => {
    const surface = document.createElement('div')

    Object.defineProperty(surface, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ left: 0, top: 0, right: 300, bottom: 120, width: 300, height: 120 }),
    })

    expect(resolveTrajectoryPlotArea(surface)).toEqual({ left: 0, width: 300 })
  })
})

describe('resolveSurfaceFrame', () => {
  it('maps pointer positions inside the plot area to the same frames the chart uses', () => {
    expect(resolveSurfaceFrame(60, 385, { left: 60, width: 682 })).toBe(0)
    expect(resolveSurfaceFrame(401, 385, { left: 60, width: 682 })).toBe(192)
    expect(resolveSurfaceFrame(742, 385, { left: 60, width: 682 })).toBe(384)
  })

  it('clamps pointer positions outside the plot area to the nearest valid frame', () => {
    expect(resolveSurfaceFrame(0, 385, { left: 60, width: 682 })).toBe(0)
    expect(resolveSurfaceFrame(900, 385, { left: 60, width: 682 })).toBe(384)
  })
})

describe('resolveSelectionHighlightStyle', () => {
  it('aligns the visible selection highlight to the chart plot area instead of the full overlay width', () => {
    const highlight = resolveSelectionHighlightStyle([215, 276], 385, { left: 60, width: 682 })

    expect(highlight).not.toBeNull()
    expect(highlight?.left).toBeCloseTo(440.9609375, 6)
    expect(highlight?.width).toBeCloseTo(110.11458333333333, 6)
  })

  it('preserves a visible width for a single-frame selection', () => {
    const highlight = resolveSelectionHighlightStyle([10, 10], 385, { left: 60, width: 682 })

    expect(highlight).not.toBeNull()
    expect(highlight?.left).toBeCloseTo(76.87239583333334, 6)
    expect(highlight?.width).toBeCloseTo(1.7760416666666667, 6)
  })
})
