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
})
