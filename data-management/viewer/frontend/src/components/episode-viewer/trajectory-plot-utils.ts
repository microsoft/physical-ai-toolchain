import type { TrajectoryAdjustment } from '@/types/episode-edit'

interface TrajectoryPointLike {
  frame: number
  timestamp: number
  jointPositions: number[]
  jointVelocities: number[]
  action?: number[]
  gripperState?: number
  signals?: Record<string, number | boolean | null | undefined>
}

interface BuildTrajectoryChartDataOptions {
  trajectoryData: readonly TrajectoryPointLike[]
  trajectoryAdjustments: ReadonlyMap<number, TrajectoryAdjustment>
  showVelocity: boolean
  showNormalized: boolean
  showAction?: boolean
}

export function applyTrajectoryAdjustment(
  value: number,
  jointIndex: number,
  adjustment: TrajectoryAdjustment | undefined,
) {
  let adjusted = value

  if (!adjustment) {
    return adjusted
  }

  if (adjustment.rightArmDelta && jointIndex >= 0 && jointIndex <= 2) {
    adjusted += adjustment.rightArmDelta[jointIndex]
  }

  if (adjustment.leftArmDelta && jointIndex >= 8 && jointIndex <= 10) {
    adjusted += adjustment.leftArmDelta[jointIndex - 8]
  }

  if (jointIndex === 7 && adjustment.rightGripperOverride !== undefined) {
    adjusted = adjustment.rightGripperOverride
  }

  if (jointIndex === 15 && adjustment.leftGripperOverride !== undefined) {
    adjusted = adjustment.leftGripperOverride
  }

  return adjusted
}

export function normalizeSeries(value: number, min: number, max: number) {
  if (max === min) {
    return 0
  }

  return (value - min) / (max - min)
}

export function signalDataKey(name: string) {
  return `signal_${name.replace(/[^a-zA-Z0-9]+/g, '_').replace(/^_+|_+$/g, '')}`
}

export function buildTrajectoryChartData({
  trajectoryData,
  trajectoryAdjustments,
  showVelocity,
  showNormalized,
  showAction = false,
}: BuildTrajectoryChartDataOptions) {
  const seriesValues = trajectoryData.map((point) => {
    const adjustment = trajectoryAdjustments.get(point.frame)

    if (showAction) {
      return point.action ?? []
    }

    return showVelocity
      ? point.jointVelocities
      : point.jointPositions.map((position, jointIndex) =>
          applyTrajectoryAdjustment(position, jointIndex, adjustment),
        )
  })

  const shouldNormalizePositions = showNormalized && !showVelocity && !showAction
  const normalizedRanges = shouldNormalizePositions
    ? (seriesValues[0]?.map((_, jointIndex) => {
        const values = seriesValues.map((pointValues) => pointValues[jointIndex])

        return {
          min: Math.min(...values),
          max: Math.max(...values),
        }
      }) ?? [])
    : []

  return trajectoryData.map((point, pointIndex) => {
    const adjustment = trajectoryAdjustments.get(point.frame)
    const data: Record<string, number | boolean> = {
      frame: point.frame,
      timestamp: point.timestamp,
      hasAdjustment: !!adjustment,
    }
    const pointValues =
      seriesValues[pointIndex] ??
      (showAction ? (point.action ?? []) : showVelocity ? point.jointVelocities : point.jointPositions)

    pointValues.forEach((value, jointIndex) => {
      const dataKey = showAction ? `action_${jointIndex}` : `joint_${jointIndex}`

      if (shouldNormalizePositions) {
        const range = normalizedRanges[jointIndex]

        data[dataKey] = range ? normalizeSeries(value, range.min, range.max) : value
        return
      }

      data[dataKey] = value
    })

    if (point.gripperState !== undefined) {
      data.gripper_state = point.gripperState
    }

    for (const [name, value] of Object.entries(point.signals ?? {})) {
      if (value === undefined || value === null) {
        continue
      }
      data[signalDataKey(name)] = typeof value === 'boolean' ? (value ? 1 : 0) : value
    }

    return data
  })
}

export function resolveTrajectorySelectionRange(
  anchorFrame: number,
  pointerFrame: number,
): [number, number] {
  return [Math.min(anchorFrame, pointerFrame), Math.max(anchorFrame, pointerFrame)]
}
