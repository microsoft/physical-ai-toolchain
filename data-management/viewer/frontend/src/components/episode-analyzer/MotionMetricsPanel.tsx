/**
 * Motion-analysis panel for the Episode Analyzer tab.
 *
 * Runs the dataviewer's trajectory-quality analysis (``POST /api/ai/trajectory-analysis``)
 * over the current episode's joint trajectory and surfaces smoothness,
 * efficiency, jitter, hesitation, corrections, and the overall motion score.
 * The ``normalized_smoothness`` metric can be computed log-scaled (default) or
 * radian-based.
 */

import { AlertCircle, Gauge, RefreshCw } from 'lucide-react'
import { memo, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import type { SmoothnessMode, TrajectoryData } from '@/hooks/use-ai-analysis'
import { useTrajectoryAnalysis } from '@/hooks/use-ai-analysis'
import { cn } from '@/lib/utils'

const MODES: { value: SmoothnessMode; label: string }[] = [
  { value: 'log-scaled', label: 'Log-scaled' },
  { value: 'radian-based', label: 'Radian-based' },
]

const SCORE_STYLES: Record<number, string> = {
  1: 'border-red-200 bg-red-50 text-red-800',
  2: 'border-orange-200 bg-orange-50 text-orange-800',
  3: 'border-yellow-200 bg-yellow-50 text-yellow-800',
  4: 'border-lime-200 bg-lime-50 text-lime-800',
  5: 'border-green-200 bg-green-50 text-green-800',
}

export interface MotionMetricsPanelProps {
  datasetId: string
  episodeId: string
  positions?: number[][]
  timestamps?: number[]
  gripperStates?: number[]
  className?: string
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="bg-muted/20 rounded-md border p-2">
      <p className="text-muted-foreground text-[11px] leading-tight">{label}</p>
      <p className="text-sm font-semibold tabular-nums">{value}</p>
      {hint && <p className="text-muted-foreground text-[10px] leading-tight">{hint}</p>}
    </div>
  )
}

export const MotionMetricsPanel = memo(function MotionMetricsPanel({
  datasetId,
  episodeId,
  positions,
  timestamps,
  gripperStates,
  className,
}: MotionMetricsPanelProps) {
  const [mode, setMode] = useState<SmoothnessMode>('log-scaled')

  const hasData = (positions?.length ?? 0) >= 3 && (timestamps?.length ?? 0) >= 3

  const trajectoryData = useMemo<TrajectoryData | undefined>(() => {
    if (!hasData || !positions || !timestamps) return undefined
    return {
      positions,
      timestamps,
      gripper_states: gripperStates,
      smoothness_mode: mode,
    }
  }, [hasData, positions, timestamps, gripperStates, mode])

  const { data, isFetching, error, refetch } = useTrajectoryAnalysis({
    datasetId,
    episodeId,
    trajectoryData,
    enabled: hasData,
  })

  const modeHint = MODES.find((m) => m.value === mode)?.label

  return (
    <section className={cn('space-y-3 rounded-md border p-3 text-sm', className)}>
      <header className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-medium">
          <Gauge className="size-4" />
          Motion Analysis
        </h3>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => void refetch()}
          disabled={!hasData || isFetching}
          aria-label="Refresh motion analysis"
          title="Refresh motion analysis"
        >
          <RefreshCw className={cn('size-4', isFetching && 'animate-spin')} />
        </Button>
      </header>

      <div className="flex items-center gap-1" role="group" aria-label="Smoothness mode">
        {MODES.map((m) => (
          <Button
            key={m.value}
            size="sm"
            variant={m.value === mode ? 'default' : 'outline'}
            className="h-7 px-2 text-xs"
            aria-pressed={m.value === mode}
            onClick={() => setMode(m.value)}
          >
            {m.label}
          </Button>
        ))}
      </div>

      {!hasData && (
        <p className="text-muted-foreground flex items-center gap-1 text-xs">
          <AlertCircle className="size-3" />
          No trajectory data available for analysis.
        </p>
      )}

      {hasData && error && (
        <p className="text-destructive flex items-center gap-1 text-xs">
          <AlertCircle className="size-3" />
          {error instanceof Error ? error.message : 'Failed to compute motion metrics.'}
        </p>
      )}

      {hasData && isFetching && !data && (
        <p className="text-muted-foreground text-xs">Computing motion metrics…</p>
      )}

      {hasData && data && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <span
              data-testid="motion-score"
              className={cn(
                'inline-flex size-8 items-center justify-center rounded-full border text-sm font-semibold',
                SCORE_STYLES[data.overall_score] ?? 'border-muted bg-muted',
              )}
            >
              {data.overall_score}
            </span>
            <span className="text-muted-foreground text-xs">Overall motion score (1–5)</span>
          </div>

          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <Metric
              label="Normalized smoothness"
              value={data.normalized_smoothness.toFixed(3)}
              hint={modeHint}
            />
            <Metric label="Raw smoothness" value={data.smoothness.toFixed(6)} />
            <Metric label="Efficiency" value={data.efficiency.toFixed(3)} />
            <Metric label="Jitter" value={data.jitter.toFixed(4)} />
            <Metric label="Hesitations" value={String(data.hesitation_count)} />
            <Metric label="Corrections" value={String(data.correction_count)} />
          </div>

          <div>
            <p className="text-muted-foreground mb-1 text-xs font-medium">Flags</p>
            {data.flags.length > 0 ? (
              <div className="flex flex-wrap gap-1">
                {data.flags.map((flag) => (
                  <span
                    key={flag}
                    className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] text-amber-800"
                  >
                    {flag}
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-muted-foreground text-xs">None detected</p>
            )}
          </div>
        </div>
      )}
    </section>
  )
})
