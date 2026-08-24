/**
 * VLM-as-Judge panel.
 *
 * Surfaces the VLM judge harness directly inside the annotation workspace.
 * The component is self-disabling when the backend reports the judge is not
 * configured (``VLM_JUDGE_ENABLED=false``), so it can ship enabled by default.
 */

import {
  AlertCircle,
  CheckCircle2,
  Database,
  Play,
  RefreshCw,
  Tag,
  Tags,
  XCircle,
} from 'lucide-react'
import { memo, useCallback, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useSaveEpisodeLabels } from '@/hooks/use-labels'
import { useRunVlmJudge, useVlmJudgeStatus } from '@/hooks/use-vlm-judge'
import { applyOutcomeLabel, outcomeToLabel, useVlmJudgeBatch } from '@/hooks/use-vlm-judge-batch'
import { cn } from '@/lib/utils'
import { useLabelStore } from '@/stores/label-store'
import type { VlmJudgeResult } from '@/types'

const METHOD_LABELS: Record<string, string> = {
  gvl: 'GVL (shuffle-and-rank)',
  chronological: 'Chronological',
}

export interface JudgePanelProps {
  datasetId: string
  episodeIndex: number
  /** Optional language instruction override; falls back to dataset metadata. */
  instruction?: string
  /** Episode count for the loaded dataset; enables the batch (all-episode) actions. */
  totalEpisodes?: number
  className?: string
}

function pickResult(
  status: ReturnType<typeof useVlmJudgeStatus>['data'],
  mutationData: VlmJudgeResult | undefined,
): VlmJudgeResult | null {
  if (mutationData) return mutationData
  if (status?.result) return status.result
  return null
}

function OutcomeBadge({ result }: { result: VlmJudgeResult }) {
  if (result.outcomeSuccess === null) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium">
        <AlertCircle className="size-3" /> Inconclusive
      </span>
    )
  }
  if (result.outcomeSuccess) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-green-200 bg-green-50 px-2 py-0.5 text-xs font-medium text-green-800">
        <CheckCircle2 className="size-3" /> SUCCESS
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-xs font-medium text-red-800">
      <XCircle className="size-3" /> FAILURE
    </span>
  )
}

function ProgressSparkline({ values }: { values: number[] }) {
  if (values.length === 0) return null
  const max = Math.max(100, ...values)
  return (
    <div className="flex h-6 items-end gap-px" aria-label="Per-frame task-completion progress">
      {values.map((v, i) => (
        <span
          key={`f${i}-${v}`}
          className="bg-primary/70 inline-block w-1.5 rounded-sm"
          style={{ height: `${Math.max(4, (v / max) * 100)}%` }}
          title={`Frame ${i}: ${v}%`}
        />
      ))}
    </div>
  )
}

function hasProcessProgress(values: number[]): boolean {
  return values.some((value) => value > 0)
}

function RunningBar({ label }: { label: string }) {
  return (
    <div className="space-y-1" role="status" aria-live="polite">
      <div
        className="bg-secondary relative h-1.5 w-full overflow-hidden rounded-full"
        role="progressbar"
        aria-label={label}
      >
        <span
          className="bg-primary absolute inset-y-0 left-0 w-2/5 rounded-full"
          style={{ animation: 'indeterminate-progress 1.2s ease-in-out infinite' }}
        />
      </div>
      <p className="text-muted-foreground text-[11px]">{label}</p>
    </div>
  )
}

function displayErrorMessage(error: Error | string): string {
  const message = error instanceof Error ? error.message : error
  if (message.includes('No task instruction available')) {
    return 'Add a Language Instruction for this episode, or save one in the dataset metadata, before running the judge.'
  }
  return message
}

export const JudgePanel = memo(function JudgePanel({
  datasetId,
  episodeIndex,
  instruction,
  totalEpisodes,
  className,
}: JudgePanelProps) {
  const status = useVlmJudgeStatus({ datasetId, episodeIndex })
  const runMutation = useRunVlmJudge()
  const saveLabels = useSaveEpisodeLabels()
  const batch = useVlmJudgeBatch(datasetId, totalEpisodes ?? 0)

  const [methodOverride, setMethodOverride] = useState<string | undefined>(undefined)
  const available = status.data?.processMethods
  const methods = available && available.length > 0 ? available : ['gvl', 'chronological']
  const effectiveMethod = methodOverride ?? status.data?.processMethod ?? 'gvl'

  const result = useMemo(
    () => pickResult(status.data, runMutation.data),
    [status.data, runMutation.data],
  )
  const enabled = status.data?.enabled !== false
  const errorMessage = useMemo(() => {
    if (runMutation.error) return displayErrorMessage(runMutation.error)
    if (status.error) return displayErrorMessage(status.error as Error)
    if (batch.error) return displayErrorMessage(batch.error)
    return null
  }, [runMutation.error, status.error, batch.error])

  const handleRun = useCallback(
    (force: boolean) => {
      runMutation.mutate({
        datasetId,
        episodeIndex,
        options: {
          instruction: instruction?.trim() || undefined,
          processMethod: effectiveMethod,
          force,
        },
      })
    },
    [runMutation, datasetId, episodeIndex, instruction, effectiveMethod],
  )

  const handleApplyLabel = useCallback(() => {
    if (!result) return
    const existing = useLabelStore.getState().episodeLabels[episodeIndex] ?? []
    const next = applyOutcomeLabel(existing, outcomeToLabel(result))
    saveLabels.mutate({ episodeIdx: episodeIndex, labels: next })
  }, [result, episodeIndex, saveLabels])

  const hasBatch = (totalEpisodes ?? 0) > 0
  const busy = runMutation.isPending || batch.isRunning || saveLabels.isPending

  if (status.isLoading) {
    return (
      <section className={cn('rounded-md border p-3 text-sm', className)} aria-busy="true">
        <header className="flex items-center justify-between">
          <h3 className="text-sm font-medium">VLM Judge</h3>
        </header>
        <p className="text-muted-foreground mt-2 text-xs">Checking judge availability…</p>
      </section>
    )
  }

  if (!enabled) {
    return (
      <section className={cn('rounded-md border p-3 text-sm', className)}>
        <header className="flex items-center justify-between">
          <h3 className="text-sm font-medium">VLM Judge</h3>
        </header>
        <p className="text-muted-foreground mt-2 text-xs">
          VLM-as-judge is not enabled for this server. Set
          <code className="bg-muted mx-1 rounded px-1 py-0.5">VLM_JUDGE_ENABLED=true</code>
          on the backend to activate it.
        </p>
      </section>
    )
  }

  return (
    <section className={cn('space-y-3 rounded-md border p-3 text-sm', className)}>
      <header className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium">VLM Judge</h3>
        {status.data?.judgeModel && (
          <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
            <Database className="size-3" />
            {status.data.judgeModel}
          </span>
        )}
      </header>

      {errorMessage && (
        <p className="text-destructive flex items-center gap-1 text-xs">
          <AlertCircle className="size-3" />
          {errorMessage}
        </p>
      )}

      {runMutation.isPending && (
        <RunningBar label="Running judge — first run may take a few minutes while the model loads…" />
      )}

      {!result && !runMutation.isPending && (
        <p className="text-muted-foreground text-xs">
          No judgment yet. Run the judge to score outcome and process reward.
        </p>
      )}

      {result && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <OutcomeBadge result={result} />
            <span className="text-muted-foreground text-xs">
              confidence {(result.outcomeConfidence * 100).toFixed(0)}% ({result.outcomeNValidVotes}{' '}
              votes)
            </span>
            {result.cached && (
              <span className="bg-muted text-muted-foreground rounded-full px-2 py-0.5 text-[10px] tracking-wide uppercase">
                cached
              </span>
            )}
          </div>

          <div>
            <p className="text-muted-foreground mb-1 text-xs font-medium">Process reward</p>
            {hasProcessProgress(result.progressPerFrame) ? (
              <ProgressSparkline values={result.progressPerFrame} />
            ) : (
              <p className="text-muted-foreground text-xs">
                Process reward unavailable for this run
              </p>
            )}
            <p className="text-muted-foreground mt-1 text-xs">
              VOC {result.voc.toFixed(2)} ({result.nFrames} frames)
            </p>
          </div>

          {result.failureMode && (
            <div>
              <p className="text-muted-foreground mb-1 text-xs font-medium">Failure mode</p>
              <p className="rounded border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-800">
                {result.failureMode.replace(/_/g, ' ')}
              </p>
            </div>
          )}

          {result.milestones.length > 0 && (
            <div>
              <p className="text-muted-foreground mb-1 text-xs font-medium">Milestones</p>
              <ul className="space-y-1">
                {result.milestones.map((m, i) => (
                  <li key={`${m.name}-${i}`} className="flex items-start gap-2 text-xs">
                    {m.completed ? (
                      <CheckCircle2 className="mt-0.5 size-3 shrink-0 text-green-600" />
                    ) : (
                      <XCircle className="mt-0.5 size-3 shrink-0 text-red-600" />
                    )}
                    <div className="flex-1">
                      <span className="font-medium">{m.name.replace(/_/g, ' ')}</span>
                      {m.frameRange && (
                        <span className="text-muted-foreground ml-1">[frames {m.frameRange}]</span>
                      )}
                      {m.evidence && <p className="text-muted-foreground mt-0.5">{m.evidence}</p>}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      <div className="space-y-1.5 border-t pt-2">
        <label
          htmlFor="vlm-process-method"
          className="text-muted-foreground block text-xs font-medium"
        >
          Scoring technique
        </label>
        <Select value={effectiveMethod} onValueChange={setMethodOverride}>
          <SelectTrigger id="vlm-process-method" className="h-7 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {methods.map((m) => (
              <SelectItem key={m} value={m} className="text-xs">
                {METHOD_LABELS[m] ?? m}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-muted-foreground text-[11px]">
          Backend: {status.data?.backend ?? '—'}
          {status.data?.nFrames != null && <span> · {status.data.nFrames} frames</span>}
        </p>
      </div>

      <div className="flex flex-wrap gap-2 pt-1">
        <Button type="button" size="sm" onClick={() => handleRun(false)} disabled={busy}>
          <Play className="mr-1 size-3" />
          {runMutation.isPending ? 'Running…' : result ? 'Re-evaluate' : 'Run judge'}
        </Button>
        {result && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => handleRun(true)}
            disabled={busy}
          >
            <RefreshCw className="mr-1 size-3" />
            Force fresh
          </Button>
        )}
        {result && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={handleApplyLabel}
            disabled={busy}
            title={`Apply ${outcomeToLabel(result)} as this episode's label`}
          >
            <Tag className="mr-1 size-3" />
            Apply label
          </Button>
        )}
      </div>

      {hasBatch && (
        <div className="space-y-2 border-t pt-2">
          <p className="text-muted-foreground text-xs font-medium">
            Whole dataset ({totalEpisodes} episodes)
          </p>
          <p className="text-muted-foreground text-[11px]">
            Uses each episode&apos;s saved or dataset instruction; the current draft instruction
            applies only to this episode.
          </p>
          {batch.progress ? (
            <div className="space-y-1">
              <div className="text-muted-foreground flex items-center justify-between text-[11px]">
                <span>
                  {batch.progress.phase === 'judging'
                    ? 'Running judge on all episodes'
                    : 'Applying labels to all episodes'}
                </span>
                <span>
                  {batch.progress.done} / {batch.progress.total}
                </span>
              </div>
              <Progress
                value={(batch.progress.done / batch.progress.total) * 100}
                className="h-1.5"
                aria-label="Batch judge progress"
              />
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-6 px-2 text-[11px]"
                onClick={batch.cancel}
              >
                Cancel
              </Button>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => void batch.runAll({ processMethod: effectiveMethod })}
                disabled={busy}
              >
                <Play className="mr-1 size-3" />
                Run all
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => void batch.applyLabelsAll({ processMethod: effectiveMethod })}
                disabled={busy}
              >
                <Tags className="mr-1 size-3" />
                Label all
              </Button>
            </div>
          )}
        </div>
      )}
    </section>
  )
})
