import fc from 'fast-check'
import { describe, expect, it } from 'vitest'

import type { FrameInsertion } from '@/types/episode-edit'

import {
  getEffectiveFrameCount,
  getEffectiveIndex,
  getOriginalIndex,
} from '../../stores/edit-store-frame-utils'

const frameInsertion: fc.Arbitrary<FrameInsertion> = fc.record({
  afterFrameIndex: fc.nat({ max: 99 }),
  interpolationFactor: fc.double({ min: 0, max: 1, noNaN: true }),
})

function buildInsertedFrames(entries: [number, FrameInsertion][]): Map<number, FrameInsertion> {
  return new Map(entries)
}

function buildRemovedFrames(indices: number[]): Set<number> {
  return new Set(indices)
}

const editScenario = fc
  .record({
    originalCount: fc.integer({ min: 1, max: 100 }),
    insertions: fc.array(fc.tuple(fc.nat({ max: 99 }), frameInsertion), { maxLength: 10 }),
    removals: fc.array(fc.nat({ max: 99 }), { maxLength: 10 }),
  })
  .map(({ originalCount, insertions, removals }) => ({
    originalCount,
    insertedFrames: buildInsertedFrames(insertions),
    removedFrames: buildRemovedFrames(removals.filter((r) => r < originalCount)),
  }))

describe('getEffectiveFrameCount', () => {
  it('is always non-negative', () => {
    fc.assert(
      fc.property(editScenario, ({ originalCount, insertedFrames, removedFrames }) => {
        const count = getEffectiveFrameCount(originalCount, insertedFrames, removedFrames)
        expect(count).toBeGreaterThanOrEqual(0)
      }),
    )
  })

  it('equals originalCount when no edits', () => {
    fc.assert(
      fc.property(fc.integer({ min: 1, max: 1000 }), (originalCount) => {
        const count = getEffectiveFrameCount(originalCount, new Map(), new Set())
        expect(count).toBe(originalCount)
      }),
    )
  })

  it('decreases by one per valid removal', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 2, max: 100 }),
        fc.integer({ min: 0, max: 98 }),
        (originalCount, removeIdx) => {
          fc.pre(removeIdx < originalCount)
          const removed = new Set([removeIdx])
          const count = getEffectiveFrameCount(originalCount, new Map(), removed)
          expect(count).toBe(originalCount - 1)
        },
      ),
    )
  })
})

describe('getEffectiveIndex roundtrip', () => {
  it('getOriginalIndex inverts getEffectiveIndex for non-inserted frames', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 50 }),
        fc.integer({ min: 0, max: 49 }),
        (originalCount, originalIndex) => {
          fc.pre(originalIndex < originalCount)
          const inserted = new Map<number, FrameInsertion>()
          const removed = new Set<number>()
          const effective = getEffectiveIndex(originalIndex, inserted, removed)
          const recovered = getOriginalIndex(effective, inserted, removed)
          expect(recovered).toBe(originalIndex)
        },
      ),
    )
  })

  it('getOriginalIndex returns null for inserted frame positions', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 3, max: 50 }),
        fc.integer({ min: 0, max: 47 }),
        (originalCount, afterIdx) => {
          fc.pre(afterIdx < originalCount - 1)
          const insertion: FrameInsertion = {
            afterFrameIndex: afterIdx,
            interpolationFactor: 0.5,
          }
          const inserted = new Map([[afterIdx, insertion]])
          const removed = new Set<number>()
          const insertedEffective = getEffectiveIndex(afterIdx, inserted, removed) + 1
          const recovered = getOriginalIndex(insertedEffective, inserted, removed)
          expect(recovered).toBeNull()
        },
      ),
    )
  })
})
