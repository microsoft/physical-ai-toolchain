/** Shared constants for joint/actuator visualization. */

export interface JointGroup {
  id: string
  label: string
  indices: number[]
}

export const OBSERVATION_LABELS: Record<number, string> = {
  0: 'Right X',
  1: 'Right Y',
  2: 'Right Z',
  3: 'Right Qx',
  4: 'Right Qy',
  5: 'Right Qz',
  6: 'Right Qw',
  7: 'Right Gripper',
  8: 'Left X',
  9: 'Left Y',
  10: 'Left Z',
  11: 'Left Qx',
  12: 'Left Qy',
  13: 'Left Qz',
  14: 'Left Qw',
  15: 'Left Gripper',
}

/** 16 distinct colors for joint visualization — no cycling needed for ≤16 joints */
export const JOINT_COLORS = [
  '#ef4444', // red — Right X
  '#f97316', // orange — Right Y
  '#eab308', // yellow — Right Z
  '#22c55e', // green — Right Qx
  '#06b6d4', // cyan — Right Qy
  '#3b82f6', // blue — Right Qz
  '#8b5cf6', // violet — Right Qw
  '#d946ef', // fuchsia — Right Gripper
  '#f43f5e', // rose — Left X
  '#fb923c', // amber — Left Y
  '#a3e635', // lime — Left Z
  '#2dd4bf', // teal — Left Qx
  '#38bdf8', // sky — Left Qy
  '#818cf8', // indigo — Left Qz
  '#c084fc', // purple — Left Qw
  '#e879f9', // pink — Left Gripper
]

/** Actuator groups for bimanual robot configuration */
export const JOINT_GROUPS: JointGroup[] = [
  { id: 'right-pos', label: 'Right Arm', indices: [0, 1, 2] },
  { id: 'right-orient', label: 'Right Orientation', indices: [3, 4, 5, 6] },
  { id: 'right-grip', label: 'Right Gripper', indices: [7] },
  { id: 'left-pos', label: 'Left Arm', indices: [8, 9, 10] },
  { id: 'left-orient', label: 'Left Orientation', indices: [11, 12, 13, 14] },
  { id: 'left-grip', label: 'Left Gripper', indices: [15] },
]

export function getJointLabel(idx: number): string {
  return OBSERVATION_LABELS[idx] || `Ch ${idx}`
}

export function getJointColor(idx: number, colors: string[] = JOINT_COLORS): string {
  return colors[idx % colors.length]
}
