import { describe, expect, it } from 'vitest'

import type { SubtaskSegment } from '../episode-edit'
import {
  createDefaultSubtask,
  generateSubtaskId,
  getNextSubtaskColor,
  rangesOverlap,
  SUBTASK_COLORS,
  validateSegments,
} from '../episode-edit'

describe('generateSubtaskId', () => {
  it('returns a string starting with "subtask-"', () => {
    expect(generateSubtaskId()).toMatch(/^subtask-/)
  })

  it('generates unique IDs', () => {
    const ids = new Set(Array.from({ length: 50 }, () => generateSubtaskId()))
    expect(ids.size).toBe(50)
  })
})

describe('getNextSubtaskColor', () => {
  it('returns the first color when no segments exist', () => {
    expect(getNextSubtaskColor([])).toBe(SUBTASK_COLORS[0])
  })

  it('returns the next unused color', () => {
    const existing: SubtaskSegment[] = [
      {
        id: '1',
        label: 'A',
        frameRange: [0, 10],
        color: SUBTASK_COLORS[0],
        source: 'manual',
      },
    ]
    expect(getNextSubtaskColor(existing)).toBe(SUBTASK_COLORS[1])
  })

  it('cycles colors when all are used', () => {
    const existing = SUBTASK_COLORS.map((color, i) => ({
      id: `${i}`,
      label: `S${i}`,
      frameRange: [i * 10, i * 10 + 9] as [number, number],
      color,
      source: 'manual' as const,
    }))
    const nextColor = getNextSubtaskColor(existing)
    expect(SUBTASK_COLORS).toContain(nextColor)
  })
})

describe('createDefaultSubtask', () => {
  it('creates a subtask with correct frame range', () => {
    const segment = createDefaultSubtask([100, 200])
    expect(segment.frameRange).toEqual([100, 200])
    expect(segment.label).toBe('Subtask 1')
    expect(segment.source).toBe('manual')
    expect(segment.id).toMatch(/^subtask-/)
  })

  it('increments label based on existing segments', () => {
    const existing: SubtaskSegment[] = [
      {
        id: '1',
        label: 'Subtask 1',
        frameRange: [0, 50],
        color: '#3b82f6',
        source: 'manual',
      },
    ]
    const segment = createDefaultSubtask([60, 100], existing)
    expect(segment.label).toBe('Subtask 2')
  })
})

describe('rangesOverlap', () => {
  it('detects overlapping ranges', () => {
    expect(rangesOverlap([0, 10], [5, 15])).toBe(true)
  })

  it('detects touching ranges as overlapping', () => {
    expect(rangesOverlap([0, 10], [10, 20])).toBe(true)
  })

  it('returns false for non-overlapping ranges', () => {
    expect(rangesOverlap([0, 10], [11, 20])).toBe(false)
  })

  it('handles reversed order', () => {
    expect(rangesOverlap([15, 20], [0, 10])).toBe(false)
  })

  it('detects containment', () => {
    expect(rangesOverlap([0, 100], [20, 40])).toBe(true)
  })
})

describe('validateSegments', () => {
  it('returns no errors for non-overlapping segments', () => {
    const segments: SubtaskSegment[] = [
      { id: '1', label: 'A', frameRange: [0, 10], color: '#000', source: 'manual' },
      { id: '2', label: 'B', frameRange: [11, 20], color: '#111', source: 'manual' },
    ]
    expect(validateSegments(segments)).toEqual([])
  })

  it('returns error for overlapping segments', () => {
    const segments: SubtaskSegment[] = [
      { id: '1', label: 'A', frameRange: [0, 10], color: '#000', source: 'manual' },
      { id: '2', label: 'B', frameRange: [5, 15], color: '#111', source: 'manual' },
    ]
    const errors = validateSegments(segments)
    expect(errors).toHaveLength(1)
    expect(errors[0]).toContain('A')
    expect(errors[0]).toContain('B')
  })

  it('returns empty for no segments', () => {
    expect(validateSegments([])).toEqual([])
  })
})
