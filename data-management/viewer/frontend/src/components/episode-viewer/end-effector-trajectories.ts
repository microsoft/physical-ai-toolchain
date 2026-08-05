import type { EpisodeData, TrajectoryVariable } from '@/types'

export type EndEffectorPoint = readonly [number, number, number]

export interface EndEffectorTrajectory {
  id: string
  label: string
  points: EndEffectorPoint[]
  lineColor: string
  markerColor: string
}

const SO101_LINKS = {
  baseHeight: 0.115,
  upperArm: 0.105,
  forearm: 0.095,
  wrist: 0.07,
}

const SO101_JOINT_NAMES = [
  'shoulder_pan.pos',
  'shoulder_lift.pos',
  'elbow_flex.pos',
  'wrist_flex.pos',
  'wrist_roll.pos',
  'gripper.pos',
]

function jointValue(joints: Record<string, number>, name: string): number {
  const camelName = name.replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase())
  return (
    joints[`${name}.pos`] ?? joints[name] ?? joints[`${camelName}.pos`] ?? joints[camelName] ?? 0
  )
}

function toRadians(value: number): number {
  return (value * Math.PI) / 180
}

export function so101EndEffectorPoint(joints: Record<string, number>): EndEffectorPoint {
  const pan = toRadians(jointValue(joints, 'shoulder_pan'))
  const shoulder = toRadians(jointValue(joints, 'shoulder_lift'))
  const elbow = shoulder + toRadians(jointValue(joints, 'elbow_flex'))
  const wrist = elbow + toRadians(jointValue(joints, 'wrist_flex'))
  const reach =
    SO101_LINKS.upperArm * Math.cos(shoulder) +
    SO101_LINKS.forearm * Math.cos(elbow) +
    SO101_LINKS.wrist * Math.cos(wrist)
  const height =
    SO101_LINKS.baseHeight +
    SO101_LINKS.upperArm * Math.sin(shoulder) +
    SO101_LINKS.forearm * Math.sin(elbow) +
    SO101_LINKS.wrist * Math.sin(wrist)
  return [reach * Math.cos(pan), height, reach * Math.sin(pan)]
}

export function buildSo101EndEffectorTrajectory(
  samples: readonly Record<string, number>[],
): EndEffectorTrajectory {
  return {
    id: 'end-effector',
    label: 'End effector',
    points: samples.map(so101EndEffectorPoint),
    lineColor: '#06b6d4',
    markerColor: '#f43f5e',
  }
}

function indexedVariables(
  variables: readonly TrajectoryVariable[],
  source: string,
): Map<number, string> {
  return new Map(
    variables
      .filter((variable) => variable.source === source && variable.index !== null)
      .map((variable) => [variable.index ?? 0, variable.key]),
  )
}

function cartesianPoints(
  episode: EpisodeData,
  keys: Map<number, string>,
  indices: readonly [number, number, number],
): EndEffectorPoint[] {
  return episode.trajectoryData.flatMap((point) => {
    const values = indices.map((index) => point.variables?.[keys.get(index) ?? ''])
    return values.every((value) => typeof value === 'number' && Number.isFinite(value))
      ? [[values[0]!, values[2]!, values[1]!] as EndEffectorPoint]
      : []
  })
}

function buildRecordedCartesianTrajectories(episode: EpisodeData): EndEffectorTrajectory[] | null {
  const variables = episode.trajectoryVariables ?? []
  const source = ['observation.state.ee_quat_pos', 'observation.state.ee_6d_pos'].find(
    (candidate) => variables.some((variable) => variable.source === candidate),
  )
  if (!source) return null

  const keys = indexedVariables(variables, source)
  const rightStart = source.endsWith('ee_quat_pos') ? 8 : 10
  const leftPoints = cartesianPoints(episode, keys, [0, 1, 2])
  const rightPoints = cartesianPoints(episode, keys, [rightStart, rightStart + 1, rightStart + 2])
  if (leftPoints.length === 0 && rightPoints.length === 0) return null

  return [
    {
      id: 'left',
      label: 'Left end effector',
      points: leftPoints,
      lineColor: '#06b6d4',
      markerColor: '#f43f5e',
    },
    {
      id: 'right',
      label: 'Right end effector',
      points: rightPoints,
      lineColor: '#8b5cf6',
      markerColor: '#f59e0b',
    },
  ]
}

function buildSo101Samples(episode: EpisodeData): Record<string, number>[] {
  const stateVariables = (episode.trajectoryVariables ?? [])
    .filter((variable) => variable.source === 'observation.state')
    .sort((left, right) => (left.index ?? 0) - (right.index ?? 0))
  const names = stateVariables.length
    ? stateVariables.map((variable) => variable.label)
    : SO101_JOINT_NAMES

  return (episode.trajectoryData ?? []).map((point) =>
    Object.fromEntries(
      names.map((name, index) => [
        name,
        stateVariables[index]
          ? (point.variables?.[stateVariables[index].key] ?? point.jointPositions[index] ?? 0)
          : (point.jointPositions[index] ?? 0),
      ]),
    ),
  )
}

export function buildEpisodeEndEffectorTrajectories(episode: EpisodeData): EndEffectorTrajectory[] {
  return (
    buildRecordedCartesianTrajectories(episode) ?? [
      buildSo101EndEffectorTrajectory(buildSo101Samples(episode)),
    ]
  )
}
