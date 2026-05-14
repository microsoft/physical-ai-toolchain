import { describe, expect, it } from 'vitest'

import type { JointGroup } from '@/components/episode-viewer/joint-constants'
import {
  getAutoSelectedJointsForEpisode,
  rankJointGroupsBySignificance,
} from '@/lib/joint-significance'
import type { TrajectoryPoint } from '@/types/api'

function makePoint(frame: number, jointPositions: number[]): TrajectoryPoint {
  return {
    timestamp: frame * 0.05,
    frame,
    jointPositions,
    jointVelocities: jointPositions.map(() => 0),
    endEffectorPose: [0, 0, 0, 0, 0, 0, 1],
    gripperState: 0,
  }
}

const POSITION_GROUP: JointGroup = { id: 'right-pos', label: 'Right Pos', indices: [0, 1, 2] }
const ORIENT_GROUP: JointGroup = {
  id: 'right-orient',
  label: 'Right Orient',
  indices: [3, 4, 5, 6],
}
const GRIP_GROUP: JointGroup = { id: 'right-grip', label: 'Right Grip', indices: [7] }
const OTHER_GROUP: JointGroup = { id: 'misc-x', label: 'Misc', indices: [8, 9] }

describe('rankJointGroupsBySignificance', () => {
  it('returns empty for empty trajectory', () => {
    expect(rankJointGroupsBySignificance([], [POSITION_GROUP], 16)).toEqual([])
  })

  it('returns empty when jointCount is 0', () => {
    const trajectory = [makePoint(0, [0, 0, 0]), makePoint(1, [1, 1, 1])]
    expect(rankJointGroupsBySignificance(trajectory, [POSITION_GROUP], 0)).toEqual([])
  })

  it('skips groups whose indices all exceed jointCount', () => {
    const trajectory = [makePoint(0, [0]), makePoint(1, [1])]
    const result = rankJointGroupsBySignificance(trajectory, [POSITION_GROUP], 0)
    expect(result).toEqual([])
  })

  it('classifies position groups and computes vector path length', () => {
    const trajectory = [
      makePoint(0, [0, 0, 0, 0, 0, 0, 1, 0]),
      makePoint(1, [1, 0, 0, 0, 0, 0, 1, 0]),
      makePoint(2, [2, 0, 0, 0, 0, 0, 1, 0]),
    ]
    const ranked = rankJointGroupsBySignificance(trajectory, [POSITION_GROUP], 16)
    expect(ranked).toHaveLength(1)
    expect(ranked[0].kind).toBe('position')
    expect(ranked[0].rawScore).toBeCloseTo(2)
    expect(ranked[0].score).toBeCloseTo(1)
  })

  it('classifies orientation groups with quaternion travel', () => {
    const trajectory = [
      makePoint(0, [0, 0, 0, 0, 0, 0, 1, 0]),
      makePoint(1, [0, 0, 0, 1, 0, 0, 0, 0]),
    ]
    const ranked = rankJointGroupsBySignificance(trajectory, [ORIENT_GROUP], 16)
    expect(ranked[0].kind).toBe('orientation')
    expect(ranked[0].rawScore).toBeCloseTo(Math.PI)
  })

  it('classifies gripper groups with scalar travel on first index', () => {
    const trajectory = [
      makePoint(0, [0, 0, 0, 0, 0, 0, 0, 0]),
      makePoint(1, [0, 0, 0, 0, 0, 0, 0, 0.5]),
    ]
    const ranked = rankJointGroupsBySignificance(trajectory, [GRIP_GROUP], 16)
    expect(ranked[0].kind).toBe('gripper')
    expect(ranked[0].rawScore).toBeCloseTo(0.5)
  })

  it('treats unknown id suffixes as other and uses vector path length', () => {
    const trajectory = [
      makePoint(0, [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]),
      makePoint(1, [0, 0, 0, 0, 0, 0, 0, 0, 1, 0]),
    ]
    const ranked = rankJointGroupsBySignificance(trajectory, [OTHER_GROUP], 16)
    expect(ranked[0].kind).toBe('other')
    expect(ranked[0].rawScore).toBeCloseTo(1)
  })

  it('zeros score when category max is below threshold', () => {
    const trajectory = [makePoint(0, [0, 0, 0]), makePoint(1, [0.001, 0, 0])]
    const ranked = rankJointGroupsBySignificance(trajectory, [POSITION_GROUP], 3)
    expect(ranked[0].rawScore).toBeCloseTo(0.001)
    expect(ranked[0].score).toBe(0)
  })

  it('sorts groups by descending score', () => {
    const trajectory = [
      makePoint(0, [0, 0, 0, 0, 0, 0, 1, 0]),
      makePoint(1, [0.5, 0, 0, 0, 0, 0, 1, 0]),
    ]
    const small: JointGroup = { id: 'a-pos', label: 'A', indices: [1] }
    const large: JointGroup = { id: 'b-pos', label: 'B', indices: [0] }
    const ranked = rankJointGroupsBySignificance(trajectory, [small, large], 16)
    expect(ranked[0].groupId).toBe('b-pos')
    expect(ranked[1].groupId).toBe('a-pos')
  })
})

describe('getAutoSelectedJointsForEpisode', () => {
  it('falls back to first group indices when no scores are positive', () => {
    const trajectory = [makePoint(0, [0, 0, 0]), makePoint(1, [0, 0, 0])]
    const groups: JointGroup[] = [POSITION_GROUP]
    expect(getAutoSelectedJointsForEpisode(trajectory, groups, 16)).toEqual([0, 1, 2])
  })

  it('filters fallback indices by jointCount', () => {
    const trajectory = [makePoint(0, [0, 0]), makePoint(1, [0, 0])]
    expect(getAutoSelectedJointsForEpisode(trajectory, [POSITION_GROUP], 2)).toEqual([0, 1])
  })

  it('returns empty when groups is empty and no fallback exists', () => {
    expect(getAutoSelectedJointsForEpisode([makePoint(0, [0])], [], 1)).toEqual([])
  })

  it('selects up to 3 groups (one per kind), sorted unique indices', () => {
    const trajectory = [
      makePoint(0, [0, 0, 0, 0, 0, 0, 1, 0]),
      makePoint(1, [1, 1, 1, 1, 0, 0, 0, 0.5]),
    ]
    const result = getAutoSelectedJointsForEpisode(
      trajectory,
      [POSITION_GROUP, ORIENT_GROUP, GRIP_GROUP],
      16,
    )
    expect(result).toEqual([0, 1, 2, 3, 4, 5, 6, 7])
    expect(new Set(result).size).toBe(result.length)
  })

  it('chooses single representative per kind', () => {
    const trajectory = [
      makePoint(0, [0, 0, 0, 0, 0, 0, 0, 0]),
      makePoint(1, [1, 0, 0, 0.5, 0, 0, 0, 0]),
    ]
    const a: JointGroup = { id: 'a-pos', label: 'A', indices: [0] }
    const b: JointGroup = { id: 'b-pos', label: 'B', indices: [3] }
    const result = getAutoSelectedJointsForEpisode(trajectory, [a, b], 16)
    expect(result).toEqual([0])
  })
})
