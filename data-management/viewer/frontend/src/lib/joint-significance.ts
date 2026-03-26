import type { JointGroup } from '@/components/episode-viewer/joint-constants'
import type { TrajectoryPoint } from '@/types/api'

const EPSILON = 1e-6
const MAX_AUTO_SELECTED_GROUPS = 3
const MIN_CATEGORY_ACTIVITY: Record<GroupKind, number> = {
  position: 0.05,
  orientation: 0.05,
  gripper: 0.01,
  other: 0.05,
}

type GroupKind = 'position' | 'orientation' | 'gripper' | 'other'

function getGroupKind(group: JointGroup): GroupKind {
  if (group.id.endsWith('-pos')) {
    return 'position'
  }

  if (group.id.endsWith('-orient')) {
    return 'orientation'
  }

  if (group.id.endsWith('-grip')) {
    return 'gripper'
  }

  return 'other'
}

function computeVectorPathLength(trajectoryData: TrajectoryPoint[], indices: number[]): number {
  let total = 0

  for (let frameIndex = 1; frameIndex < trajectoryData.length; frameIndex += 1) {
    let squaredDistance = 0

    for (const index of indices) {
      const delta =
        (trajectoryData[frameIndex].jointPositions[index] ?? 0) -
        (trajectoryData[frameIndex - 1].jointPositions[index] ?? 0)
      squaredDistance += delta * delta
    }

    total += Math.sqrt(squaredDistance)
  }

  return total
}

function normalizeQuaternion(values: number[]): [number, number, number, number] {
  const magnitude = Math.hypot(values[0], values[1], values[2], values[3])
  if (magnitude < EPSILON) {
    return [0, 0, 0, 1]
  }

  return [
    values[0] / magnitude,
    values[1] / magnitude,
    values[2] / magnitude,
    values[3] / magnitude,
  ]
}

function computeQuaternionTravel(trajectoryData: TrajectoryPoint[], indices: number[]): number {
  let total = 0

  for (let frameIndex = 1; frameIndex < trajectoryData.length; frameIndex += 1) {
    const previous = normalizeQuaternion(
      indices.map((index) => trajectoryData[frameIndex - 1].jointPositions[index] ?? 0),
    )
    const current = normalizeQuaternion(
      indices.map((index) => trajectoryData[frameIndex].jointPositions[index] ?? 0),
    )

    const dot = Math.min(
      1,
      Math.max(
        -1,
        previous[0] * current[0] +
          previous[1] * current[1] +
          previous[2] * current[2] +
          previous[3] * current[3],
      ),
    )
    total += 2 * Math.acos(Math.abs(dot))
  }

  return total
}

function computeScalarTravel(trajectoryData: TrajectoryPoint[], index: number): number {
  let total = 0

  for (let frameIndex = 1; frameIndex < trajectoryData.length; frameIndex += 1) {
    total += Math.abs(
      (trajectoryData[frameIndex].jointPositions[index] ?? 0) -
        (trajectoryData[frameIndex - 1].jointPositions[index] ?? 0),
    )
  }

  return total
}

function computeGroupRawScore(
  trajectoryData: TrajectoryPoint[],
  group: JointGroup,
  jointCount: number,
): number {
  const indices = group.indices.filter((index) => index < jointCount)
  if (!indices.length || trajectoryData.length < 2) {
    return 0
  }

  switch (getGroupKind(group)) {
    case 'position':
      return computeVectorPathLength(trajectoryData, indices)
    case 'orientation':
      return computeQuaternionTravel(trajectoryData, indices)
    case 'gripper':
      return computeScalarTravel(trajectoryData, indices[0])
    default:
      return computeVectorPathLength(trajectoryData, indices)
  }
}

export interface JointGroupSignificance {
  groupId: string
  label: string
  indices: number[]
  score: number
  rawScore: number
  kind: GroupKind
}

export function rankJointGroupsBySignificance(
  trajectoryData: TrajectoryPoint[],
  groups: JointGroup[],
  jointCount: number,
): JointGroupSignificance[] {
  if (!trajectoryData.length || jointCount === 0) {
    return []
  }

  const rawGroups = groups
    .map((group) => {
      const indices = group.indices.filter((index) => index < jointCount)
      if (!indices.length) {
        return null
      }

      return {
        groupId: group.id,
        label: group.label,
        indices,
        rawScore: computeGroupRawScore(trajectoryData, group, jointCount),
        kind: getGroupKind(group),
      }
    })
    .filter((group): group is Omit<JointGroupSignificance, 'score'> => group !== null)

  const maxScoresByKind = rawGroups.reduce<Record<GroupKind, number>>(
    (accumulator, group) => {
      accumulator[group.kind] = Math.max(accumulator[group.kind], group.rawScore)
      return accumulator
    },
    { position: 0, orientation: 0, gripper: 0, other: 0 },
  )

  return rawGroups
    .map((group) => {
      const categoryMax = maxScoresByKind[group.kind]
      const score =
        categoryMax >= MIN_CATEGORY_ACTIVITY[group.kind]
          ? group.rawScore / Math.max(categoryMax, EPSILON)
          : 0

      return {
        ...group,
        score,
      }
    })
    .sort((left, right) => right.score - left.score)
}

export function getAutoSelectedJointsForEpisode(
  trajectoryData: TrajectoryPoint[],
  groups: JointGroup[],
  jointCount: number,
): number[] {
  const rankedGroups = rankJointGroupsBySignificance(trajectoryData, groups, jointCount)

  if (!rankedGroups.some((group) => group.score > 0)) {
    return groups[0]?.indices.filter((index) => index < jointCount) ?? []
  }

  const selectedByKind = new Map<GroupKind, JointGroupSignificance>()
  for (const group of rankedGroups) {
    if (group.score <= 0 || selectedByKind.has(group.kind)) {
      continue
    }

    selectedByKind.set(group.kind, group)
  }

  const selected = Array.from(selectedByKind.values())
    .sort((left, right) => right.score - left.score || right.rawScore - left.rawScore)
    .slice(0, MAX_AUTO_SELECTED_GROUPS)

  if (!selected.length) {
    return groups[0]?.indices.filter((index) => index < jointCount) ?? []
  }

  return Array.from(new Set(selected.flatMap((group) => group.indices))).sort(
    (left, right) => left - right,
  )
}
