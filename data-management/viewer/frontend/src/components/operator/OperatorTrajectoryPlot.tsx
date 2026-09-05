import { useMemo } from 'react'

import type { OperatorTelemetry } from '@/api/operator'
import { EndEffectorTrajectoryPlot } from '@/components/episode-viewer'
import { buildSo101EndEffectorTrajectory } from '@/components/episode-viewer/end-effector-trajectories'

interface OperatorTrajectoryPlotProps {
  samples: OperatorTelemetry[]
}

export function OperatorTrajectoryPlot({ samples }: OperatorTrajectoryPlotProps) {
  const trajectories = useMemo(
    () => [buildSo101EndEffectorTrajectory(samples.map((sample) => sample.follower))],
    [samples],
  )

  return (
    <EndEffectorTrajectoryPlot
      trajectories={trajectories}
      emptyMessage="Spatial path appears while a session is running"
    />
  )
}
