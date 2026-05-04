import fc from 'fast-check'
import { describe, expect, it } from 'vitest'

import type { JointGroup } from '@/components/episode-viewer/joint-constants'
import { rankJointGroupsBySignificance } from '@/lib/joint-significance'
import type { TrajectoryPoint } from '@/types/api'

function buildTrajectory(values: number[][]): TrajectoryPoint[] {
  return values.map((jointPositions, frame) => ({
    timestamp: frame * 0.05,
    frame,
    jointPositions,
    jointVelocities: jointPositions.map(() => 0),
    endEffectorPose: [0, 0, 0, 0, 0, 0, 1],
    gripperState: 0,
  }))
}

const POSITION_GROUP: JointGroup = { id: 'g-pos', label: 'G', indices: [0, 1, 2] }
const NUM_RUNS = 100

describe('rankJointGroupsBySignificance properties', () => {
  it('produces deterministic results for identical inputs', () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.tuple(
            fc.float({ min: -10, max: 10, noNaN: true }),
            fc.float({ min: -10, max: 10, noNaN: true }),
            fc.float({ min: -10, max: 10, noNaN: true }),
          ),
          { minLength: 2, maxLength: 30 },
        ),
        (frames) => {
          const trajectory = buildTrajectory(frames.map((f) => [f[0], f[1], f[2]]))
          const a = rankJointGroupsBySignificance(trajectory, [POSITION_GROUP], 3)
          const b = rankJointGroupsBySignificance(trajectory, [POSITION_GROUP], 3)
          expect(a).toEqual(b)
        },
      ),
      { numRuns: NUM_RUNS },
    )
  })

  it('keeps normalized score within [0, 1]', () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.tuple(
            fc.float({ min: -5, max: 5, noNaN: true }),
            fc.float({ min: -5, max: 5, noNaN: true }),
            fc.float({ min: -5, max: 5, noNaN: true }),
          ),
          { minLength: 2, maxLength: 25 },
        ),
        (frames) => {
          const trajectory = buildTrajectory(frames.map((f) => [f[0], f[1], f[2]]))
          const ranked = rankJointGroupsBySignificance(trajectory, [POSITION_GROUP], 3)
          for (const g of ranked) {
            expect(g.score).toBeGreaterThanOrEqual(0)
            expect(g.score).toBeLessThanOrEqual(1 + 1e-9)
          }
        },
      ),
      { numRuns: NUM_RUNS },
    )
  })

  it('scales rawScore linearly with positive uniform scaling for position groups', () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.tuple(
            fc.float({ min: -3, max: 3, noNaN: true }),
            fc.float({ min: -3, max: 3, noNaN: true }),
            fc.float({ min: -3, max: 3, noNaN: true }),
          ),
          { minLength: 2, maxLength: 20 },
        ),
        fc.float({ min: Math.fround(0.1), max: 10, noNaN: true }),
        (frames, scale) => {
          const base = buildTrajectory(frames.map((f) => [f[0], f[1], f[2]]))
          const scaled = buildTrajectory(
            frames.map((f) => [f[0] * scale, f[1] * scale, f[2] * scale]),
          )
          const baseScore =
            rankJointGroupsBySignificance(base, [POSITION_GROUP], 3)[0]?.rawScore ?? 0
          const scaledScore =
            rankJointGroupsBySignificance(scaled, [POSITION_GROUP], 3)[0]?.rawScore ?? 0
          expect(scaledScore).toBeCloseTo(baseScore * scale, 4)
        },
      ),
      { numRuns: NUM_RUNS },
    )
  })
})
