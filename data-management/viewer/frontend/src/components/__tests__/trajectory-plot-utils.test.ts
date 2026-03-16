import { describe, expect, it } from 'vitest'

import {
  applyTrajectoryAdjustment,
  buildTrajectoryChartData,
  normalizeSeries,
  resolveTrajectorySelectionRange,
} from '@/components/episode-viewer/trajectory-plot-utils'

describe('trajectory-plot-utils', () => {
  it('applies joint adjustments and normalizes chart data for position mode', () => {
    const chartData = buildTrajectoryChartData({
      trajectoryData: [
        {
          frame: 0,
          timestamp: 0,
          jointPositions: [0, 2],
          jointVelocities: [0, 0.2],
        },
        {
          frame: 1,
          timestamp: 0.1,
          jointPositions: [1, 4],
          jointVelocities: [0.1, 0.4],
        },
      ],
      trajectoryAdjustments: new Map([[1, { frameIndex: 1, rightArmDelta: [1, 2, 0] }]]),
      showVelocity: false,
      showNormalized: true,
    })

    expect(chartData[0]).toMatchObject({ frame: 0, joint_0: 0, joint_1: 0 })
    expect(chartData[1]).toMatchObject({ frame: 1, joint_0: 1, joint_1: 1 })
  })

  it('returns raw velocity data when velocity mode is active', () => {
    const chartData = buildTrajectoryChartData({
      trajectoryData: [
        {
          frame: 0,
          timestamp: 0,
          jointPositions: [0, 2],
          jointVelocities: [0.1, 0.2],
        },
      ],
      trajectoryAdjustments: new Map(),
      showVelocity: true,
      showNormalized: true,
    })

    expect(chartData[0]).toMatchObject({ joint_0: 0.1, joint_1: 0.2 })
  })

  it('normalizes values and resolves drag selection ranges predictably', () => {
    expect(normalizeSeries(6, 2, 10)).toBe(0.5)
    expect(applyTrajectoryAdjustment(3, 0, { frameIndex: 0, rightArmDelta: [2, 0, 0] })).toBe(5)
    expect(resolveTrajectorySelectionRange(8, 3)).toEqual([3, 8])
  })
})
