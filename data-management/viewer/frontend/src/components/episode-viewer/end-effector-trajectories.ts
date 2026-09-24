import type { EpisodeData, TrajectoryVariable } from '@/types'

export const MAX_END_EFFECTOR_POINTS = 2_000

export type EndEffectorPoint = readonly [number, number, number]

export interface EndEffectorTrajectory {
  id: string
  label: string
  points: EndEffectorPoint[]
  lineColor: string
  markerColor: string
}

export interface EndEffectorViewBounds {
  center: EndEffectorPoint
  maxSpan: number
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

const SO101_KINEMATIC_JOINTS = ['shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex']

export function getEndEffectorViewBounds(
  trajectories: readonly EndEffectorTrajectory[],
): EndEffectorViewBounds | null {
  const points = trajectories
    .flatMap((trajectory) => trajectory.points)
    .filter((point) => point.every(Number.isFinite))
  if (points.length === 0) return null

  const minimum = [...points[0]]
  const maximum = [...points[0]]
  for (const point of points.slice(1)) {
    for (const axis of [0, 1, 2] as const) {
      minimum[axis] = Math.min(minimum[axis], point[axis])
      maximum[axis] = Math.max(maximum[axis], point[axis])
    }
  }

  return {
    center: [
      (minimum[0] + maximum[0]) / 2,
      (minimum[1] + maximum[1]) / 2,
      (minimum[2] + maximum[2]) / 2,
    ],
    maxSpan: Math.max(...maximum.map((value, axis) => value - minimum[axis])),
  }
}

function boundPoints(points: EndEffectorPoint[]): EndEffectorPoint[] {
  if (points.length <= MAX_END_EFFECTOR_POINTS) return points

  const interval = (points.length - 1) / (MAX_END_EFFECTOR_POINTS - 1)
  return Array.from(
    { length: MAX_END_EFFECTOR_POINTS },
    (_, index) => points[Math.round(index * interval)],
  )
}

function jointAliases(name: string): string[] {
  const camelName = name.replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase())
  return [`${name}.pos`, name, `${camelName}.pos`, camelName]
}

function jointValue(joints: Record<string, number>, name: string): number | undefined {
  return jointAliases(name)
    .map((alias) => joints[alias])
    .find((value) => value !== undefined)
}

function toRadians(value: number): number {
  return (value * Math.PI) / 180
}

export function so101EndEffectorPoint(joints: Record<string, number>): EndEffectorPoint | null {
  const values = SO101_KINEMATIC_JOINTS.map((name) => jointValue(joints, name))
  if (
    !values.every((value): value is number => typeof value === 'number' && Number.isFinite(value))
  ) {
    return null
  }

  const [panValue, shoulderValue, elbowValue, wristValue] = values
  const pan = toRadians(panValue)
  const shoulder = toRadians(shoulderValue)
  const elbow = shoulder + toRadians(elbowValue)
  const wrist = elbow + toRadians(wristValue)
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
  const points = samples.flatMap((sample) => {
    const result = so101EndEffectorPoint(sample)
    return result ? [result] : []
  })
  return {
    id: 'end-effector',
    label: 'End effector',
    points: boundPoints(points),
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
      .filter(
        (variable) =>
          variable.source === source &&
          typeof variable.index === 'number' &&
          Number.isInteger(variable.index) &&
          variable.index >= 0,
      )
      .map((variable) => [variable.index as number, variable.key]),
  )
}

function cartesianPoints(
  episode: EpisodeData,
  keys: Map<number, string>,
  indices: readonly [number, number, number],
): EndEffectorPoint[] {
  const points = episode.trajectoryData.flatMap((point) => {
    const values = indices.map((index) => point.variables?.[keys.get(index) ?? ''])
    return values.every(
      (value): value is number => typeof value === 'number' && Number.isFinite(value),
    )
      ? ([[values[0], values[2], values[1]]] as EndEffectorPoint[])
      : []
  })
  return boundPoints(points)
}

function buildRecordedCartesianTrajectories(episode: EpisodeData): EndEffectorTrajectory[] | null {
  const variables = episode.trajectoryVariables ?? []
  const source = ['observation.state.ee_quat_pos', 'observation.state.ee_6d_pos'].find(
    (candidate) => variables.some((variable) => variable.source === candidate),
  )
  if (!source) return null

  const keys = indexedVariables(variables, source)
  const rightStart = source.endsWith('ee_quat_pos') ? 8 : 10
  const trajectories = [
    {
      id: 'left',
      label: 'Left end effector',
      points: cartesianPoints(episode, keys, [0, 1, 2]),
      lineColor: '#06b6d4',
      markerColor: '#f43f5e',
    },
    {
      id: 'right',
      label: 'Right end effector',
      points: cartesianPoints(episode, keys, [rightStart, rightStart + 1, rightStart + 2]),
      lineColor: '#2563eb',
      markerColor: '#f59e0b',
    },
  ].filter((trajectory) => trajectory.points.length > 0)

  return trajectories.length > 0 ? trajectories : null
}

function buildDirectCartesianTrajectory(episode: EpisodeData): EndEffectorTrajectory[] | null {
  const points = boundPoints(
    episode.trajectoryData.flatMap((point) => {
      const [x, y, z] = point.endEffectorPose ?? []
      return [x, y, z].every(
        (value): value is number => typeof value === 'number' && Number.isFinite(value),
      )
        ? ([[x, z, y]] as EndEffectorPoint[])
        : []
    }),
  )
  if (points.length === 0) return null

  return [
    {
      id: 'end-effector',
      label: 'End effector',
      points,
      lineColor: '#06b6d4',
      markerColor: '#f43f5e',
    },
  ]
}

function buildSo101Samples(episode: EpisodeData): Record<string, number>[] | null {
  const stateVariables = (episode.trajectoryVariables ?? [])
    .filter((variable) => variable.source === 'observation.state')
    .sort((left, right) => (left.index ?? 0) - (right.index ?? 0))
  const stateLabels = new Set(stateVariables.map((variable) => variable.label))
  if (
    stateVariables.length > 0 &&
    !SO101_KINEMATIC_JOINTS.every((name) =>
      jointAliases(name).some((alias) => stateLabels.has(alias)),
    )
  ) {
    return null
  }
  if (
    stateVariables.length === 0 &&
    (episode.trajectoryData[0]?.jointPositions.length ?? 0) !== SO101_JOINT_NAMES.length
  ) {
    return null
  }

  const names = stateVariables.length
    ? stateVariables.map((variable) => variable.label)
    : SO101_JOINT_NAMES
  const samples = episode.trajectoryData.flatMap((point) => {
    const sample = Object.fromEntries(
      names.flatMap((name, index) => {
        const variable = stateVariables[index]
        const value = variable
          ? (point.variables?.[variable.key] ?? point.jointPositions[index])
          : point.jointPositions[index]
        return typeof value === 'number' && Number.isFinite(value) ? [[name, value]] : []
      }),
    )
    return SO101_KINEMATIC_JOINTS.every((name) => jointValue(sample, name) !== undefined)
      ? [sample]
      : []
  })

  return samples.length > 0 ? samples : null
}

export function buildEpisodeEndEffectorTrajectories(episode: EpisodeData): EndEffectorTrajectory[] {
  const recorded =
    buildRecordedCartesianTrajectories(episode) ?? buildDirectCartesianTrajectory(episode)
  if (recorded) return recorded

  const samples = buildSo101Samples(episode)
  if (!samples) return []

  const trajectory = buildSo101EndEffectorTrajectory(samples)
  return trajectory.points.length > 0 ? [trajectory] : []
}
