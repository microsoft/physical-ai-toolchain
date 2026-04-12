import { describe, expect, it } from 'vitest'

import type { FrameInsertion } from '@/types/episode-edit'

import {
  getEffectiveFrameCount,
  getEffectiveIndex,
  getOriginalIndex,
} from '../edit-store-frame-utils'

describe('edit-store-frame-utils', () => {
  it('offsets effective indexes around insertions and removals', () => {
    const inserted = new Map<number, FrameInsertion>([
      [2, { afterFrameIndex: 2, interpolationFactor: 0.5 }],
    ])
    const removed = new Set([4])

    expect(getEffectiveIndex(6, inserted, removed)).toBe(6)
  })

  it('returns null when the effective index points at an inserted frame', () => {
    const inserted = new Map<number, FrameInsertion>([
      [2, { afterFrameIndex: 2, interpolationFactor: 0.5 }],
    ])

    expect(getOriginalIndex(3, inserted, new Set())).toBeNull()
  })

  it('ignores invalid insertions when computing effective frame count', () => {
    const inserted = new Map<number, FrameInsertion>([
      [5, { afterFrameIndex: 5, interpolationFactor: 0.5 }],
      [9, { afterFrameIndex: 9, interpolationFactor: 0.5 }],
    ])

    expect(getEffectiveFrameCount(10, inserted, new Set([5]))).toBe(9)
  })

  describe('getEffectiveIndex edge cases', () => {
    it('returns identity when maps are empty', () => {
      expect(getEffectiveIndex(5, new Map(), new Set())).toBe(5)
    })

    it('returns 0 for index 0 with no preceding operations', () => {
      const inserted = new Map<number, FrameInsertion>([
        [3, { afterFrameIndex: 3, interpolationFactor: 0.5 }],
      ])
      expect(getEffectiveIndex(0, inserted, new Set([5]))).toBe(0)
    })

    it('handles removal before index shifting it left', () => {
      expect(getEffectiveIndex(5, new Map(), new Set([2]))).toBe(4)
    })

    it('handles insertion before index shifting it right', () => {
      const inserted = new Map<number, FrameInsertion>([
        [1, { afterFrameIndex: 1, interpolationFactor: 0.5 }],
      ])
      expect(getEffectiveIndex(5, inserted, new Set())).toBe(6)
    })

    it('handles both insertion and removal before the same index', () => {
      const inserted = new Map<number, FrameInsertion>([
        [1, { afterFrameIndex: 1, interpolationFactor: 0.5 }],
      ])
      expect(getEffectiveIndex(5, inserted, new Set([2]))).toBe(5)
    })
  })

  describe('getOriginalIndex edge cases', () => {
    it('returns identity when maps are empty', () => {
      expect(getOriginalIndex(5, new Map(), new Set())).toBe(5)
    })

    it('returns 0 for effective index 0 with no preceding operations', () => {
      expect(getOriginalIndex(0, new Map(), new Set())).toBe(0)
    })

    it('accounts for removed frames when mapping back', () => {
      expect(getOriginalIndex(3, new Map(), new Set([1]))).toBe(4)
    })

    it('returns null for multiple inserted frame positions', () => {
      const inserted = new Map<number, FrameInsertion>([
        [1, { afterFrameIndex: 1, interpolationFactor: 0.5 }],
        [4, { afterFrameIndex: 4, interpolationFactor: 0.5 }],
      ])
      expect(getOriginalIndex(2, inserted, new Set())).toBeNull()
      expect(getOriginalIndex(6, inserted, new Set())).toBeNull()
    })
  })

  describe('getEffectiveFrameCount edge cases', () => {
    it('returns original count when maps are empty', () => {
      expect(getEffectiveFrameCount(10, new Map(), new Set())).toBe(10)
    })

    it('returns 0 for empty original', () => {
      expect(getEffectiveFrameCount(0, new Map(), new Set())).toBe(0)
    })

    it('subtracts all removed frames', () => {
      expect(getEffectiveFrameCount(5, new Map(), new Set([0, 1, 2]))).toBe(2)
    })

    it('does not count insertions at the last frame boundary', () => {
      const inserted = new Map<number, FrameInsertion>([
        [9, { afterFrameIndex: 9, interpolationFactor: 0.5 }],
      ])
      expect(getEffectiveFrameCount(10, inserted, new Set())).toBe(10)
    })

    it('counts insertions at valid interior positions', () => {
      const inserted = new Map<number, FrameInsertion>([
        [0, { afterFrameIndex: 0, interpolationFactor: 0.5 }],
        [4, { afterFrameIndex: 4, interpolationFactor: 0.5 }],
      ])
      expect(getEffectiveFrameCount(10, inserted, new Set())).toBe(12)
    })

    it('does not count insertions at removed positions', () => {
      const inserted = new Map<number, FrameInsertion>([
        [3, { afterFrameIndex: 3, interpolationFactor: 0.5 }],
      ])
      expect(getEffectiveFrameCount(10, inserted, new Set([3]))).toBe(9)
    })
  })
})
