import { describe, expect, it } from 'vitest'

import type { TrajectoryAdjustment } from '@/types/episode-edit'

import {
  applyTrajectoryAdjustment,
  buildTrajectoryChartData,
  normalizeSeries,
  resolveTrajectorySelectionRange,
} from '../trajectory-plot-utils'

describe('applyTrajectoryAdjustment', () => {
  it('returns the input value unchanged when no adjustment is provided', () => {
    expect(applyTrajectoryAdjustment(0.42, 0, undefined)).toBe(0.42)
    expect(applyTrajectoryAdjustment(0.42, 7, undefined)).toBe(0.42)
  })

  it('adds rightArmDelta to right-arm position joints (indices 0-2)', () => {
    const adjustment: TrajectoryAdjustment = { frameIndex: 0, rightArmDelta: [0.1, 0.2, 0.3] }
    expect(applyTrajectoryAdjustment(1, 0, adjustment)).toBeCloseTo(1.1)
    expect(applyTrajectoryAdjustment(1, 1, adjustment)).toBeCloseTo(1.2)
    expect(applyTrajectoryAdjustment(1, 2, adjustment)).toBeCloseTo(1.3)
  })

  it('adds leftArmDelta to left-arm position joints (indices 8-10) with offset', () => {
    const adjustment: TrajectoryAdjustment = { frameIndex: 0, leftArmDelta: [0.5, 0.6, 0.7] }
    expect(applyTrajectoryAdjustment(2, 8, adjustment)).toBeCloseTo(2.5)
    expect(applyTrajectoryAdjustment(2, 9, adjustment)).toBeCloseTo(2.6)
    expect(applyTrajectoryAdjustment(2, 10, adjustment)).toBeCloseTo(2.7)
  })

  it('replaces the value at index 7 with rightGripperOverride', () => {
    const adjustment: TrajectoryAdjustment = { frameIndex: 0, rightGripperOverride: 0.99 }
    expect(applyTrajectoryAdjustment(0.1, 7, adjustment)).toBe(0.99)
  })

  it('replaces the value at index 15 with leftGripperOverride', () => {
    const adjustment: TrajectoryAdjustment = { frameIndex: 0, leftGripperOverride: -0.42 }
    expect(applyTrajectoryAdjustment(0.1, 15, adjustment)).toBe(-0.42)
  })

  it('does not modify joint indices outside the targeted ranges', () => {
    const adjustment: TrajectoryAdjustment = {
      frameIndex: 0,
      rightArmDelta: [0.1, 0.2, 0.3],
      leftArmDelta: [0.4, 0.5, 0.6],
    }
    expect(applyTrajectoryAdjustment(0.5, 3, adjustment)).toBe(0.5)
    expect(applyTrajectoryAdjustment(0.5, 6, adjustment)).toBe(0.5)
    expect(applyTrajectoryAdjustment(0.5, 11, adjustment)).toBe(0.5)
    expect(applyTrajectoryAdjustment(0.5, 14, adjustment)).toBe(0.5)
  })
})

describe('normalizeSeries', () => {
  it('returns 0 when min equals max', () => {
    expect(normalizeSeries(5, 5, 5)).toBe(0)
  })

  it('linearly normalizes a value within [min, max] to [0, 1]', () => {
    expect(normalizeSeries(0, 0, 10)).toBe(0)
    expect(normalizeSeries(10, 0, 10)).toBe(1)
  })

  it('returns 0.5 for the midpoint of the range', () => {
    expect(normalizeSeries(5, 0, 10)).toBe(0.5)
  })
})

interface TestTrajectoryPoint {
  frame: number
  timestamp: number
  jointPositions: number[]
  jointVelocities: number[]
}

function makeJointArray(value: number): number[] {
  const arr = new Array(16).fill(0) as number[]
  arr[0] = value
  return arr
}

function makeTrajectory(): TestTrajectoryPoint[] {
  return [
    {
      frame: 0,
      timestamp: 0,
      jointPositions: makeJointArray(0),
      jointVelocities: makeJointArray(10),
    },
    {
      frame: 1,
      timestamp: 0.1,
      jointPositions: makeJointArray(1),
      jointVelocities: makeJointArray(20),
    },
    {
      frame: 2,
      timestamp: 0.2,
      jointPositions: makeJointArray(2),
      jointVelocities: makeJointArray(30),
    },
  ]
}

describe('buildTrajectoryChartData', () => {
  it('builds positions data with adjustments applied per frame', () => {
    const trajectoryData = makeTrajectory()
    const trajectoryAdjustments = new Map<number, TrajectoryAdjustment>([
      [1, { frameIndex: 1, rightArmDelta: [0.5, 0, 0] }],
    ])

    const result = buildTrajectoryChartData({
      trajectoryData,
      trajectoryAdjustments,
      showVelocity: false,
      showNormalized: false,
    })

    expect(result).toHaveLength(3)
    expect(result[0].joint_0).toBe(0)
    expect(result[0].hasAdjustment).toBe(false)
    expect(result[1].joint_0).toBeCloseTo(1.5)
    expect(result[1].hasAdjustment).toBe(true)
    expect(result[2].joint_0).toBe(2)
  })

  it('uses raw velocity values without normalization or adjustments when showVelocity is true', () => {
    const trajectoryData = makeTrajectory()
    const trajectoryAdjustments = new Map<number, TrajectoryAdjustment>([
      [1, { frameIndex: 1, rightArmDelta: [0.5, 0, 0] }],
    ])

    const result = buildTrajectoryChartData({
      trajectoryData,
      trajectoryAdjustments,
      showVelocity: true,
      showNormalized: true,
    })

    expect(result[0].joint_0).toBe(10)
    expect(result[1].joint_0).toBe(20)
    expect(result[2].joint_0).toBe(30)
  })

  it('normalizes per-joint to [0, 1] when showNormalized is true', () => {
    const trajectoryData = makeTrajectory()
    const result = buildTrajectoryChartData({
      trajectoryData,
      trajectoryAdjustments: new Map(),
      showVelocity: false,
      showNormalized: true,
    })

    expect(result[0].joint_0).toBe(0)
    expect(result[1].joint_0).toBe(0.5)
    expect(result[2].joint_0).toBe(1)
  })
})

describe('resolveTrajectorySelectionRange', () => {
  it('returns [anchor, pointer] when anchor is less than pointer', () => {
    expect(resolveTrajectorySelectionRange(2, 5)).toEqual([2, 5])
  })

  it('returns [pointer, anchor] when anchor is greater than pointer', () => {
    expect(resolveTrajectorySelectionRange(8, 3)).toEqual([3, 8])
  })

  it('returns [n, n] when anchor equals pointer', () => {
    expect(resolveTrajectorySelectionRange(4, 4)).toEqual([4, 4])
  })
})
