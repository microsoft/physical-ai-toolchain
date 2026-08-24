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

  it('builds normalized chart series from named trajectory variables', () => {
    const chartData = buildTrajectoryChartData({
      trajectoryData: [
        {
          frame: 0,
          timestamp: 0,
          jointPositions: [0],
          jointVelocities: [0],
          variables: {
            'observation.state[0]': 0,
            'action[0]': 2,
            'observation.gripper.is_closed': 0,
          },
        },
        {
          frame: 1,
          timestamp: 0.1,
          jointPositions: [1],
          jointVelocities: [0.1],
          variables: {
            'observation.state[0]': 1,
            'action[0]': 4,
            'observation.gripper.is_closed': 1,
          },
        },
      ],
      trajectoryAdjustments: new Map(),
      trajectoryVariables: [
        { key: 'observation.state[0]', label: 'shoulder_pan_joint', source: 'observation.state' },
        { key: 'action[0]', label: 'target_shoulder_pan_joint', source: 'action' },
        {
          key: 'observation.gripper.is_closed',
          label: 'is_closed',
          source: 'observation.gripper.is_closed',
        },
      ],
      showVelocity: false,
      showNormalized: true,
    })

    expect(chartData[0]).toMatchObject({ series_0: 0, series_1: 0, series_2: 0 })
    expect(chartData[1]).toMatchObject({ series_0: 1, series_1: 1, series_2: 1 })
  })

  it('normalizes values and resolves drag selection ranges predictably', () => {
    expect(normalizeSeries(6, 2, 10)).toBe(0.5)
    expect(applyTrajectoryAdjustment(3, 0, { frameIndex: 0, rightArmDelta: [2, 0, 0] })).toBe(5)
    expect(resolveTrajectorySelectionRange(8, 3)).toEqual([3, 8])
  })
})
