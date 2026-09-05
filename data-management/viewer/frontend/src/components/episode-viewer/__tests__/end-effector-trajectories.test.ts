import { describe, expect, it } from 'vitest'

import type { EpisodeData, TrajectoryPoint, TrajectoryVariable } from '@/types'

import {
  buildEpisodeEndEffectorTrajectories,
  getEndEffectorViewBounds,
} from '../end-effector-trajectories'

function point(
  variables: Record<string, number>,
  jointPositions: number[] = [],
  endEffectorPose: number[] = [],
): TrajectoryPoint {
  return {
    timestamp: 0,
    frame: 0,
    jointPositions,
    jointVelocities: [],
    endEffectorPose,
    gripperState: 0,
    variables,
  }
}

function episode(
  trajectoryVariables: TrajectoryVariable[],
  trajectoryData: TrajectoryPoint[],
): EpisodeData {
  return {
    meta: { index: 0, length: trajectoryData.length, taskIndex: 0, hasAnnotations: false },
    cameras: [],
    videoUrls: {},
    trajectoryVariables,
    trajectoryData,
  }
}

describe('buildEpisodeEndEffectorTrajectories', () => {
  it('uses recorded HDF5 end-effector poses before joint-based fallback', () => {
    const variables = Array.from({ length: 14 }, (_, index) => ({
      key: `observation.state[${index}]`,
      label: `State ${index + 1}`,
      source: 'observation.state',
      index,
      kind: 'state',
    }))
    const trajectoryData = [
      point({}, Array(14).fill(0), [0.257, 1.007, 0.238, 0, 0, 0, 1]),
      point({}, Array(14).fill(0), [Number.NaN, 0.5, 0.3, 0, 0, 0, 1]),
      point({}, Array(14).fill(0), [0.562, 0.174, 0.361, 0, 0, 0, 1]),
    ]

    const trajectories = buildEpisodeEndEffectorTrajectories(episode(variables, trajectoryData))

    expect(trajectories).toEqual([
      expect.objectContaining({
        id: 'end-effector',
        label: 'End effector',
        points: [
          [0.257, 0.238, 1.007],
          [0.562, 0.361, 0.174],
        ],
      }),
    ])
  })

  it('derives view bounds that frame metre-scale HDF5 trajectories', () => {
    const trajectories = [
      {
        id: 'end-effector',
        label: 'End effector',
        points: [
          [0.257, 0.238, 1.007],
          [0.562, 0.361, 0.174],
        ] as [number, number, number][],
        lineColor: '#06b6d4',
        markerColor: '#f43f5e',
      },
    ]

    const bounds = getEndEffectorViewBounds(trajectories)

    expect(bounds).not.toBeNull()
    expect(bounds?.center[0]).toBeCloseTo(0.4095)
    expect(bounds?.center[1]).toBeCloseTo(0.2995)
    expect(bounds?.center[2]).toBeCloseTo(0.5905)
    expect(bounds?.maxSpan).toBeCloseTo(0.833)
  })

  it('uses recorded ALOHA Cartesian poses for both arms', () => {
    const source = 'observation.state.ee_quat_pos'
    const variables = Array.from({ length: 16 }, (_, index) => ({
      key: `${source}[${index}]`,
      label: `pose ${index}`,
      source,
      index,
      kind: 'signal',
    }))
    const frame = point(
      Object.fromEntries(
        Array.from({ length: 16 }, (_, index) => [`${source}[${index}]`, index / 10]),
      ),
      [],
      [9, 9, 9, 0, 0, 0, 1],
    )

    const trajectories = buildEpisodeEndEffectorTrajectories(episode(variables, [frame]))

    expect(trajectories).toEqual([
      expect.objectContaining({ id: 'left', label: 'Left end effector', points: [[0, 0.2, 0.1]] }),
      expect.objectContaining({
        id: 'right',
        label: 'Right end effector',
        points: [[0.8, 1, 0.9]],
      }),
    ])
  })

  it('derives one SO-101 path when Cartesian poses are unavailable', () => {
    const source = 'observation.state'
    const labels = [
      'shoulder_pan.pos',
      'shoulder_lift.pos',
      'elbow_flex.pos',
      'wrist_flex.pos',
      'wrist_roll.pos',
      'gripper.pos',
    ]
    const variables = labels.map((label, index) => ({
      key: `${source}[${index}]`,
      label,
      source,
      index,
      kind: 'state',
    }))

    const trajectories = buildEpisodeEndEffectorTrajectories(
      episode(variables, [point({}, [0, -90, 90, 45, 0, 0])]),
    )

    expect(trajectories).toHaveLength(1)
    expect(trajectories[0]).toMatchObject({ id: 'end-effector', label: 'End effector' })
    expect(trajectories[0].points).toHaveLength(1)
  })

  it('does not invent an SO-101 path for an unknown joint schema', () => {
    const variables = Array.from({ length: 14 }, (_, index) => ({
      key: `observation.state[${index}]`,
      label: `State ${index + 1}`,
      source: 'observation.state',
      index,
      kind: 'state',
    }))

    const trajectories = buildEpisodeEndEffectorTrajectories(
      episode(variables, [point({}, Array(14).fill(0))]),
    )

    expect(trajectories).toEqual([])
  })
})
