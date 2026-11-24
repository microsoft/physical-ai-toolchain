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
  channelIndex: number,
  adjustment: TrajectoryAdjustment | undefined,
) {
  const setValue = adjustment?.channelValues?.[channelIndex]
  if (setValue !== undefined) {
    return setValue
  }

  return value + (adjustment?.channelDeltas?.[channelIndex] ?? 0)
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
  trajectoryAdjustments: ReadonlyMap<number, TrajectoryAdjustment>,
) {
  return trajectoryData.map((point) => {
    const adjustment = trajectoryAdjustments.get(point.frame)

    return trajectoryVariables.map((variable) => {
      const raw = point.variables?.[variable.key]
      const value = typeof raw === 'number' && Number.isFinite(raw) ? raw : 0
      // State variable `index` is the same channel the raw joint-position view adjusts.
      return variable.kind === 'state' && typeof variable.index === 'number'
        ? applyTrajectoryAdjustment(value, variable.index, adjustment)
        : value
    })
  })
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
    ? buildNamedVariableValues(trajectoryData, trajectoryVariables, trajectoryAdjustments)
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
