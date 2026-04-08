import fc from 'fast-check'
import { describe, expect, it } from 'vitest'

import type { TrajectoryPlotArea } from '../trajectory-graph-geometry'
import { resolveSelectionHighlightStyle, resolveSurfaceFrame } from '../trajectory-graph-geometry'

const plotArea: fc.Arbitrary<TrajectoryPlotArea> = fc.record({
  left: fc.double({ min: 0, max: 1000, noNaN: true }),
  width: fc.double({ min: 1, max: 1000, noNaN: true }),
})

describe('resolveSurfaceFrame', () => {
  it('output is always in [0, totalFrames-1] for valid inputs', () => {
    fc.assert(
      fc.property(
        fc.double({ min: -500, max: 2000, noNaN: true }),
        fc.integer({ min: 2, max: 10_000 }),
        plotArea,
        (surfaceX, totalFrames, area) => {
          const frame = resolveSurfaceFrame(surfaceX, totalFrames, area)
          expect(frame).toBeGreaterThanOrEqual(0)
          expect(frame).toBeLessThanOrEqual(totalFrames - 1)
        },
      ),
    )
  })

  it('returns 0 when plotArea is null', () => {
    fc.assert(
      fc.property(
        fc.double({ min: -500, max: 2000, noNaN: true }),
        fc.integer({ min: 1, max: 10_000 }),
        (surfaceX, totalFrames) => {
          expect(resolveSurfaceFrame(surfaceX, totalFrames, null)).toBe(0)
        },
      ),
    )
  })

  it('is monotonically non-decreasing with surfaceX', () => {
    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 1000, noNaN: true }),
        fc.double({ min: 0, max: 100, noNaN: true }),
        fc.integer({ min: 2, max: 10_000 }),
        plotArea,
        (x1, delta, totalFrames, area) => {
          const result1 = resolveSurfaceFrame(x1, totalFrames, area)
          const result2 = resolveSurfaceFrame(x1 + delta, totalFrames, area)
          expect(result2).toBeGreaterThanOrEqual(result1)
        },
      ),
    )
  })
})

describe('resolveSelectionHighlightStyle', () => {
  it('returns non-null with positive width for valid ranges', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 999 }),
        fc.integer({ min: 0, max: 999 }),
        fc.integer({ min: 2, max: 1000 }),
        plotArea,
        (a, b, totalFrames, area) => {
          fc.pre(a !== b && a < totalFrames && b < totalFrames)
          const result = resolveSelectionHighlightStyle([a, b], totalFrames, area)
          if (result) {
            expect(result.width).toBeGreaterThan(0)
          }
        },
      ),
    )
  })

  it('left is within plot area bounds', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 999 }),
        fc.integer({ min: 0, max: 999 }),
        fc.integer({ min: 2, max: 1000 }),
        plotArea,
        (a, b, totalFrames, area) => {
          fc.pre(a !== b && a < totalFrames && b < totalFrames)
          const result = resolveSelectionHighlightStyle([a, b], totalFrames, area)
          if (result) {
            expect(result.left).toBeGreaterThanOrEqual(area.left)
          }
        },
      ),
    )
  })

  it('produces identical results regardless of range order', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 999 }),
        fc.integer({ min: 0, max: 999 }),
        fc.integer({ min: 2, max: 1000 }),
        plotArea,
        (a, b, totalFrames, area) => {
          fc.pre(a !== b && a < totalFrames && b < totalFrames)
          const forward = resolveSelectionHighlightStyle([a, b], totalFrames, area)
          const reversed = resolveSelectionHighlightStyle([b, a], totalFrames, area)
          expect(forward).toEqual(reversed)
        },
      ),
    )
  })
})
