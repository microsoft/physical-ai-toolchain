import { describe, expect, it } from 'vitest'

import type { EpisodeData, TrajectoryPoint, TrajectoryVariable } from '@/types'

import {
  buildEpisodeEndEffectorTrajectories,
  getEndEffectorViewBounds,
  MAX_END_EFFECTOR_POINTS,
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
  it('prefers recorded Cartesian variables over per-frame poses', () => {
    const source = 'observation.state.ee_quat_pos'
    const variables = Array.from({ length: 16 }, (_, index) => ({
      key: `${source}[${index}]`,
      label: `pose ${index}`,
      source,
      index,
      kind: 'signal',
    }))
    const values = Object.fromEntries(
      Array.from({ length: 16 }, (_, index) => [`${source}[${index}]`, index / 10]),
    )

    const trajectories = buildEpisodeEndEffectorTrajectories(
      episode(variables, [point(values, [], [9, 9, 9, 0, 0, 0, 1])]),
    )

    expect(trajectories).toEqual([
      expect.objectContaining({ id: 'left', points: [[0, 0.2, 0.1]] }),
      expect.objectContaining({ id: 'right', points: [[0.8, 1, 0.9]] }),
    ])
  })

  it('uses valid per-frame poses before SO-101 kinematics', () => {
    const trajectories = buildEpisodeEndEffectorTrajectories(
      episode([], [point({}, [0, -90, 90, 45, 0, 0], [0.257, 1.007, 0.238, 0, 0, 0, 1])]),
    )

    expect(trajectories).toEqual([
      expect.objectContaining({ id: 'end-effector', points: [[0.257, 0.238, 1.007]] }),
    ])
  })

  it('derives SO-101 points only from a recognized finite joint schema', () => {
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

    const valid = buildEpisodeEndEffectorTrajectories(
      episode(variables, [point({}, [0, -90, 90, 45, 0, 0])]),
    )
    const malformed = buildEpisodeEndEffectorTrajectories(
      episode(variables, [point({}, [0, Number.NaN, 90, 45, 0, 0])]),
    )

    expect(valid[0]?.points).toHaveLength(1)
    expect(malformed).toEqual([])
  })

  it('does not fabricate a trace for an unknown joint schema', () => {
    const variables = Array.from({ length: 14 }, (_, index) => ({
      key: `observation.state[${index}]`,
      label: `State ${index + 1}`,
      source: 'observation.state',
      index,
      kind: 'state',
    }))

    expect(
      buildEpisodeEndEffectorTrajectories(episode(variables, [point({}, Array(14).fill(0))])),
    ).toEqual([])
  })

  it('bounds rendered points while retaining view bounds across the path', () => {
    const trajectoryData = Array.from({ length: MAX_END_EFFECTOR_POINTS + 20 }, (_, index) =>
      point({}, [], [index, index * 2, index * 3]),
    )

    const trajectories = buildEpisodeEndEffectorTrajectories(episode([], trajectoryData))
    const bounds = getEndEffectorViewBounds(trajectories)

    expect(trajectories[0].points).toHaveLength(MAX_END_EFFECTOR_POINTS)
    expect(trajectories[0].points.at(0)).toEqual([0, 0, 0])
    expect(trajectories[0].points.at(-1)).toEqual([
      MAX_END_EFFECTOR_POINTS + 19,
      (MAX_END_EFFECTOR_POINTS + 19) * 3,
      (MAX_END_EFFECTOR_POINTS + 19) * 2,
    ])
    expect(bounds?.maxSpan).toBe((MAX_END_EFFECTOR_POINTS + 19) * 3)
  })
})
