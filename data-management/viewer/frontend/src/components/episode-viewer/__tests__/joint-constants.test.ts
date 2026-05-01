import { describe, expect, it } from 'vitest'

import {
  getJointColor,
  getJointLabel,
  JOINT_COLORS,
  JOINT_GROUPS,
  OBSERVATION_LABELS,
} from '../joint-constants'

describe('joint-constants', () => {
  it('defines 16 observation labels and 16 joint colors', () => {
    expect(Object.keys(OBSERVATION_LABELS)).toHaveLength(16)
    expect(JOINT_COLORS).toHaveLength(16)
  })

  it('JOINT_GROUPS covers indices 0-15 exactly once across all groups', () => {
    const all = JOINT_GROUPS.flatMap((g) => g.indices).sort((a, b) => a - b)
    expect(all).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15])
    expect(new Set(all).size).toBe(16)
  })

  it('getJointLabel returns the mapped label for known indices', () => {
    expect(getJointLabel(0)).toBe('Right X')
    expect(getJointLabel(15)).toBe('Left Gripper')
  })

  it('getJointLabel returns "Ch N" for unknown indices', () => {
    expect(getJointLabel(99)).toBe('Ch 99')
  })

  it('getJointColor cycles by modulo when using a custom palette', () => {
    expect(getJointColor(0)).toBe(JOINT_COLORS[0])
    expect(getJointColor(5, ['#a', '#b'])).toBe('#b')
    expect(getJointColor(4, ['#a', '#b'])).toBe('#a')
  })
})
