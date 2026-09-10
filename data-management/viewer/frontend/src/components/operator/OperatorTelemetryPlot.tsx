import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { OperatorTelemetry } from '@/api/operator'

interface OperatorTelemetryPlotProps {
  samples: OperatorTelemetry[]
}

const JOINT_ORDER = [
  'shoulder_pan',
  'shoulder_lift',
  'elbow_flex',
  'wrist_flex',
  'wrist_roll',
  'gripper',
]

const JOINT_COLORS = [
  'hsl(var(--chart-1))',
  'hsl(var(--chart-2))',
  'hsl(var(--chart-3))',
  'hsl(var(--chart-4))',
  'hsl(var(--chart-5))',
  'hsl(var(--chart-6))',
]

function jointStem(key: string): string {
  return key
    .replace(/\.pos$/, '')
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .toLowerCase()
}

function jointLabel(key: string): string {
  const label = jointStem(key).replaceAll('_', ' ')
  return label.charAt(0).toUpperCase() + label.slice(1)
}

interface JointTooltipPayload {
  dataKey?: string | number
  value?: number | string
}

function JointTooltip({
  active,
  label,
  payload,
}: {
  active?: boolean
  label?: number | string
  payload?: JointTooltipPayload[]
}) {
  if (!active || !payload?.length) return null
  const latest = new Map<string, JointTooltipPayload>()
  for (const item of payload) {
    const dataKey = String(item.dataKey ?? '')
    const role = dataKey.split(':')[0]
    if (role) latest.set(role, item)
  }
  const actuatorKey = String(latest.get('commanded')?.dataKey ?? '').split(':')[1] ?? ''
  return (
    <div className="bg-popover text-popover-foreground min-w-36 border p-2 text-xs shadow-md">
      <p className="mb-1 font-medium">
        {jointLabel(actuatorKey)} · {Number(label).toFixed(2)} s
      </p>
      {(['leader', 'follower', 'commanded'] as const).map((role) => {
        const value = latest.get(role)?.value
        return value == null ? null : (
          <p key={role} className="flex justify-between gap-3 capitalize">
            <span className="text-muted-foreground">{role}</span>
            <span className="font-mono">{Number(value).toFixed(2)}</span>
          </p>
        )
      })}
    </div>
  )
}

export function OperatorTelemetryPlot({ samples }: OperatorTelemetryPlotProps) {
  const joints = Array.from(
    new Set(
      samples.flatMap((sample) => [
        ...Object.keys(sample.leader),
        ...Object.keys(sample.follower),
        ...Object.keys(sample.commanded),
      ]),
    ),
  ).sort((left, right) => {
    const leftOrder = JOINT_ORDER.indexOf(jointStem(left))
    const rightOrder = JOINT_ORDER.indexOf(jointStem(right))
    return (
      (leftOrder < 0 ? JOINT_ORDER.length : leftOrder) -
        (rightOrder < 0 ? JOINT_ORDER.length : rightOrder) || left.localeCompare(right)
    )
  })

  if (joints.length === 0) {
    return (
      <section aria-label="Joint states" className="space-y-2">
        <h3 className="text-sm font-semibold">Joint states</h3>
        <div
          aria-label="Joint state plot"
          className="text-muted-foreground flex h-80 items-center justify-center border text-sm"
        >
          Sensor telemetry appears while a session is running
        </div>
      </section>
    )
  }

  const data = samples.map((sample) => {
    const point: Record<string, number> = { elapsed: Number(sample.elapsedS.toFixed(2)) }
    for (const joint of joints) {
      point[`leader:${joint}`] = sample.leader[joint]
      point[`follower:${joint}`] = sample.follower[joint]
      point[`commanded:${joint}`] = sample.commanded[joint]
    }
    return point
  })

  return (
    <section aria-label="Joint states" className="min-w-0 space-y-2">
      <h3 className="text-sm font-semibold">Joint states</h3>
      <div aria-label="Joint state plot" className="flex h-80 min-w-0 flex-col border">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b px-3 py-2">
          <div className="grid flex-1 grid-cols-2 gap-x-3 gap-y-1 sm:grid-cols-3 xl:grid-cols-6">
            {joints.map((joint, index) => (
              <div key={joint} className="flex min-w-0 items-center gap-1.5 text-xs">
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-sm"
                  style={{ backgroundColor: JOINT_COLORS[index % JOINT_COLORS.length] }}
                />
                <span className="truncate">{jointLabel(joint)}</span>
              </div>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <span aria-label="Leader values use dashed lines" className="flex items-center gap-1.5">
              <span className="border-foreground w-5 border-t border-dashed" />
              Leader
            </span>
            <span
              aria-label="Follower values use solid lines"
              className="flex items-center gap-1.5"
            >
              <span className="bg-foreground h-px w-5" />
              Follower
            </span>
            <span
              aria-label="Commanded values use circle markers"
              className="flex items-center gap-1.5"
            >
              <span className="border-foreground bg-background h-2 w-2 rounded-full border" />
              Commanded
            </span>
          </div>
        </div>
        <div className="min-h-0 flex-1 p-2">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
              <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" />
              <XAxis
                dataKey="elapsed"
                domain={['dataMin', 'dataMax']}
                stroke="hsl(var(--muted-foreground))"
                tick={{ fontSize: 11 }}
                type="number"
                unit="s"
              />
              <YAxis stroke="hsl(var(--muted-foreground))" tick={{ fontSize: 11 }} width={46} />
              <Tooltip content={<JointTooltip />} />
              {joints.flatMap((joint, index) => {
                const color = JOINT_COLORS[index % JOINT_COLORS.length]
                return [
                  <Line
                    key={`leader:${joint}`}
                    dataKey={`leader:${joint}`}
                    dot={false}
                    isAnimationActive={false}
                    name={`${jointLabel(joint)} leader`}
                    stroke={color}
                    strokeDasharray="6 4"
                    strokeWidth={1.25}
                    type="monotone"
                  />,
                  <Line
                    key={`follower:${joint}`}
                    dataKey={`follower:${joint}`}
                    dot={false}
                    isAnimationActive={false}
                    name={`${jointLabel(joint)} follower`}
                    stroke={color}
                    strokeWidth={2}
                    type="monotone"
                  />,
                  <Line
                    key={`commanded:${joint}`}
                    dataKey={`commanded:${joint}`}
                    dot={{
                      r: 2.2,
                      fill: color,
                      stroke: 'hsl(var(--background))',
                      strokeWidth: 1.5,
                    }}
                    isAnimationActive={false}
                    legendType="circle"
                    name={`${jointLabel(joint)} commanded`}
                    stroke="none"
                    type="monotone"
                  />,
                ]
              })}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </section>
  )
}
