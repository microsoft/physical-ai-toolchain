import { useMemo } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, XAxis, YAxis } from 'recharts'

import { EndEffectorTrajectoryPlot } from '@/components/episode-viewer'
import { buildSo101EndEffectorTrajectory } from '@/components/episode-viewer/end-effector-trajectories'
import type { OperatorTelemetry } from '@/types'

interface OperatorMonitoringProps {
  samples: OperatorTelemetry[]
}

const MAX_RENDERED_SAMPLES = 180

export function OperatorMonitoring({ samples }: OperatorMonitoringProps) {
  const boundedSamples = samples.slice(-MAX_RENDERED_SAMPLES)
  const joint = Object.keys(boundedSamples.at(-1)?.follower ?? {})[0]
  const chartData = boundedSamples.map((sample) => ({
    elapsed: sample.elapsedS,
    leader: joint ? sample.leader[joint] : undefined,
    follower: joint ? sample.follower[joint] : undefined,
    commanded: joint ? sample.commanded[joint] : undefined,
  }))
  const trajectory = useMemo(
    () => buildSo101EndEffectorTrajectory(boundedSamples.map((sample) => sample.follower)),
    [boundedSamples],
  )

  return (
    <div className="grid min-w-0 gap-4 xl:grid-cols-2">
      <section className="min-w-0 space-y-2" aria-labelledby="operator-telemetry-heading">
        <h3 id="operator-telemetry-heading" className="text-sm font-semibold">
          Joint telemetry
        </h3>
        <div className="bg-muted/20 h-64 min-w-0 border p-2">
          {!joint ? (
            <div className="text-muted-foreground flex h-full items-center justify-center px-4 text-center text-sm">
              Telemetry appears while a session is running
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
                <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" />
                <XAxis dataKey="elapsed" type="number" domain={['dataMin', 'dataMax']} />
                <YAxis width={44} />
                <Line dataKey="leader" dot={false} isAnimationActive={false} stroke="#0891b2" />
                <Line dataKey="follower" dot={false} isAnimationActive={false} stroke="#2563eb" />
                <Line dataKey="commanded" dot={false} isAnimationActive={false} stroke="#e11d48" />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
      </section>
      <section className="min-w-0 space-y-2" aria-labelledby="operator-trajectory-heading">
        <h3 id="operator-trajectory-heading" className="text-sm font-semibold">
          End-effector trajectory
        </h3>
        {trajectory.points.length === 0 ? (
          <div className="bg-muted/20 text-muted-foreground flex aspect-video w-full items-center justify-center border px-4 text-center text-sm">
            Trajectory appears while a session is running
          </div>
        ) : (
          <EndEffectorTrajectoryPlot trajectories={[trajectory]} frameClassName="border" />
        )}
      </section>
    </div>
  )
}
