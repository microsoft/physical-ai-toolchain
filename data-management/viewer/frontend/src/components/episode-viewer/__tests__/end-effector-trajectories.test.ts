import { describe, expect, it } from 'vitest'

import type { EpisodeData, TrajectoryPoint, TrajectoryVariable } from '@/types'

import { buildEpisodeEndEffectorTrajectories } from '../end-effector-trajectories'

function point(variables: Record<string, number>, jointPositions: number[] = []): TrajectoryPoint {
  return {
    timestamp: 0,
    frame: 0,
    jointPositions,
    jointVelocities: [],
    endEffectorPose: [],
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
})
