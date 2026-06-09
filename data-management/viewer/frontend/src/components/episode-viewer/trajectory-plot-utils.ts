import type { TrajectoryAdjustment } from '@/types/episode-edit'

interface TrajectoryPointLike {
  frame: number
  timestamp: number
  jointPositions: number[]
  jointVelocities: number[]
  variables?: Record<string, number | null | undefined>
}

export interface TrajectoryVariableLike {
  key: string
  label: string
  source: string
  index?: number | null
  kind?: string
}

interface BuildTrajectoryChartDataOptions {
  trajectoryData: readonly TrajectoryPointLike[]
  trajectoryAdjustments: ReadonlyMap<number, TrajectoryAdjustment>
  trajectoryVariables?: readonly TrajectoryVariableLike[]
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

function normalizeSeriesValues(seriesValues: number[][]) {
  return (
    seriesValues[0]?.map((_, seriesIndex) => {
      const values = seriesValues.map((pointValues) => pointValues[seriesIndex])

      return {
        min: Math.min(...values),
        max: Math.max(...values),
      }
    }) ?? []
  )
}

function buildNamedVariableValues(
  trajectoryData: readonly TrajectoryPointLike[],
  trajectoryVariables: readonly TrajectoryVariableLike[],
) {
  return trajectoryData.map((point) =>
    trajectoryVariables.map((variable) => {
      const value = point.variables?.[variable.key]
      return typeof value === 'number' && Number.isFinite(value) ? value : 0
    }),
  )
}

export function buildTrajectoryChartData({
  trajectoryData,
  trajectoryAdjustments,
  trajectoryVariables = [],
  showVelocity,
  showNormalized,
}: BuildTrajectoryChartDataOptions) {
  const shouldUseNamedVariables = !showVelocity && trajectoryVariables.length > 0
  const seriesValues = shouldUseNamedVariables
    ? buildNamedVariableValues(trajectoryData, trajectoryVariables)
    : trajectoryData.map((point) => {
      const adjustment = trajectoryAdjustments.get(point.frame)

      return showVelocity
        ? point.jointVelocities
        : point.jointPositions.map((position, jointIndex) =>
          applyTrajectoryAdjustment(position, jointIndex, adjustment),
        )
    })

  const shouldNormalizePositions = showNormalized && !showVelocity
  const normalizedRanges = shouldNormalizePositions ? normalizeSeriesValues(seriesValues) : []

  return trajectoryData.map((point, pointIndex) => {
    const adjustment = trajectoryAdjustments.get(point.frame)
    const data: Record<string, number | boolean> = {
      frame: point.frame,
      timestamp: point.timestamp,
      hasAdjustment: !!adjustment,
    }
    const pointValues =
      seriesValues[pointIndex] ?? (showVelocity ? point.jointVelocities : point.jointPositions)

    pointValues.forEach((value, seriesIndex) => {
      const dataKey = shouldUseNamedVariables ? `series_${seriesIndex}` : `joint_${seriesIndex}`

      if (shouldNormalizePositions) {
        const range = normalizedRanges[seriesIndex]

        data[dataKey] = range ? normalizeSeries(value, range.min, range.max) : value
        return
      }

      data[dataKey] = value
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
