import type { TrajectoryAdjustment } from '@/types/episode-edit'

interface TrajectoryPointLike {
  frame: number
  timestamp: number
  jointPositions: number[]
  jointVelocities: number[]
}

interface BuildTrajectoryChartDataOptions {
  trajectoryData: readonly TrajectoryPointLike[]
  trajectoryAdjustments: ReadonlyMap<number, TrajectoryAdjustment>
  showVelocity: boolean
  showNormalized: boolean
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

export function buildTrajectoryChartData({
  trajectoryData,
  trajectoryAdjustments,
  showVelocity,
  showNormalized,
}: BuildTrajectoryChartDataOptions) {
  const seriesValues = trajectoryData.map((point) => {
    const adjustment = trajectoryAdjustments.get(point.frame)

    return showVelocity
      ? point.jointVelocities
      : point.jointPositions.map((position, jointIndex) =>
          applyTrajectoryAdjustment(position, jointIndex, adjustment),
        )
  })

  const shouldNormalizePositions = showNormalized && !showVelocity
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
      seriesValues[pointIndex] ?? (showVelocity ? point.jointVelocities : point.jointPositions)

    pointValues.forEach((value, jointIndex) => {
      if (shouldNormalizePositions) {
        const range = normalizedRanges[jointIndex]

        data[`joint_${jointIndex}`] = range ? normalizeSeries(value, range.min, range.max) : value
        return
      }

      data[`joint_${jointIndex}`] = value
    })

    return data
  })
}

export function resolveTrajectorySelectionRange(
  anchorFrame: number,
  pointerFrame: number,
): [number, number] {
  return [Math.min(anchorFrame, pointerFrame), Math.max(anchorFrame, pointerFrame)]
}
