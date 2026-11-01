/**
 * VLM-as-Judge panel.
 *
 * Surfaces the VLM judge harness directly inside the annotation workspace.
 * The component is self-disabling when the backend reports the judge is not
 * configured (``VLM_JUDGE_ENABLED=false``), so it can ship enabled by default.
 */

import { AlertCircle, CheckCircle2, Database, Play, RefreshCw, Tag, XCircle } from 'lucide-react'
import { memo, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useCapabilities } from '@/hooks/use-datasets'
import { useEpisodeReadiness } from '@/hooks/use-episode-readiness'
import {
  useApplyJudgeResult,
  useJudgeEvidence,
  useRunVlmJudge,
  useVlmJudgeStatus,
} from '@/hooks/use-vlm-judge'
import { outcomeToLabel } from '@/hooks/use-vlm-judge-batch'
import { ApiClientError } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import type { VlmJudgeResult } from '@/types'

const METHOD_LABELS: Record<string, string> = {
  gvl: 'GVL (shuffle-and-rank)',
  chronological: 'Chronological',
}

export interface JudgePanelProps {
  datasetId: string
  episodeIndex: number
  cameras?: string[]
  totalEpisodes?: number
  className?: string
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

function applicationErrorMessage(error: Error): string {
  if (error instanceof ApiClientError) {
    if (error.status === 409 || error.status === 412) {
      return 'Label application conflicts with newer saved inputs or withdrawn evidence. Refresh saved evidence, then judge the current inputs again if needed.'
    }
    if (error.status === 400 || error.status === 422) {
      return 'The server rejected the label application request as invalid. Refreshing will not resolve this; record Diagnostics and report the issue.'
    }
    if (error.status === 401 || error.status === 403) {
      return 'You are not authorized to apply this label. Sign in again or request access.'
    }
    if (error.status === 404) {
      return 'The judge run was not found. Refresh saved evidence before retrying.'
    }
    return 'Label application failed on the server. Retry later; saved evidence is unchanged.'
  }
  return `Label application failed: ${error.message}`
}

export const JudgePanel = memo(function JudgePanel({
  datasetId,
  episodeIndex,
  cameras = [],
  className,
}: JudgePanelProps) {
  const capabilities = useCapabilities(datasetId)
  const judgeEnabled = capabilities.data?.vlmJudgeEnabled === true
  const status = useVlmJudgeStatus({ datasetId, episodeIndex, enabled: judgeEnabled })
  const runMutation = useRunVlmJudge()
  const application = useApplyJudgeResult()
  const evidence = useJudgeEvidence(datasetId, episodeIndex, judgeEnabled)
  const readiness = useEpisodeReadiness(datasetId, [episodeIndex], judgeEnabled)

  const [methodOverride, setMethodOverride] = useState<string | undefined>(undefined)
  const [viewSelection, setViewSelection] = useState<{ datasetId: string; views: string[] } | null>(
    null,
  )
  const judgeViews = (
    viewSelection?.datasetId === datasetId ? viewSelection.views : cameras
  ).filter((camera) => cameras.includes(camera))
  const cameraOptions = judgeViews.length ? { views: judgeViews } : {}
  const viewsReady = cameras.length === 0 || judgeViews.length > 0
  const available = status.data?.processMethods
  const methods = available && available.length > 0 ? available : ['gvl', 'chronological']
  const effectiveMethod = methodOverride ?? status.data?.processMethod ?? 'gvl'

  const assessment = evidence.data?.items.find(
    (item) => item.resultKind === 'judge' && item.applicability !== 'withdrawn',
  )
  const result = assessment?.resultKind === 'judge' ? assessment.result : null
  const enabled = judgeEnabled && status.data?.enabled !== false
  const errorMessage = useMemo(() => {
    if (runMutation.error) return displayErrorMessage(runMutation.error)
    if (status.error) return displayErrorMessage(status.error as Error)
    return null
  }, [runMutation.error, status.error])

  const handleRun = (force: boolean) => {
    if (!enabled || !readiness.ready || !viewsReady) return
    runMutation.mutate({
      datasetId,
      episodeIndex,
      options: {
        ...cameraOptions,
        processMethod: effectiveMethod,
        force,
      },
    })
  }

  const handleApplyLabel = () => {
    if (
      !enabled ||
      !readiness.ready ||
      !result ||
      assessment?.applicability !== 'current' ||
      result.outcomeSuccess === null
    )
      return
    application.mutate({ datasetId, episodeIndex, runId: assessment.runId })
  }

  const busy = runMutation.isPending || application.isPending

  if (capabilities.isLoading || (judgeEnabled && status.isLoading)) {
    return (
      <section className={cn('text-sm', className)} aria-busy="true">
        <header className="flex items-center justify-between">
          <h3 className="text-sm font-medium">VLM Judge</h3>
        </header>
        <p className="text-muted-foreground mt-2 text-xs">Checking judge availability…</p>
      </section>
    )
  }

  if (!enabled && !evidence.data?.items.length) {
    return (
      <section className={cn('text-sm', className)}>
        <header className="flex items-center justify-between">
          <h3 className="text-sm font-medium">VLM Judge</h3>
        </header>
        <p className="text-muted-foreground mt-2 text-xs">
          VLM-as-judge is not enabled for this server. Set
          <code className="bg-muted text-foreground mx-1 rounded px-1 py-0.5">
            VLM_JUDGE_ENABLED=true
          </code>
          on the backend to activate it.
        </p>
      </section>
    )
  }

  return (
    <section aria-label="Judge assessment" className={cn('space-y-3 text-sm', className)}>
      <header className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium">Judge assessment</h3>
        {status.data?.judgeModel && (
          <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
            <Database className="size-3" />
            {status.data.judgeModel}
          </span>
        )}
      </header>

      {evidence.error && (
        <div role="alert" className="text-sm">
          Saved evidence refresh failed. Previously loaded evidence remains visible.
          <Button size="sm" variant="outline" onClick={() => void evidence.refetch()}>
            Retry evidence
          </Button>
        </div>
      )}
      {application.error && <p role="alert">{applicationErrorMessage(application.error)}</p>}
      {assessment && (
        <p className="text-xs break-all">
          Run {assessment.runId}; model {result?.judgeModel}; input {assessment.input.snapshotId};{' '}
          {assessment.applicability}
        </p>
      )}
      {(evidence.data?.items.length ?? 0) > 0 && (
        <details>
          <summary>Run history</summary>
          <ul className="space-y-2 text-xs">
            {evidence.data?.items.map((item) => (
              <li key={item.resultId ?? item.runId} className="break-all">
                {item.runId}: {item.resultKind}, {item.applicability},{' '}
                {item.applied ? 'applied' : 'not applied'}; input {item.input.snapshotId}
                <p>Model: {item.result.judgeModel}</p>
                {item.resultKind === 'judge' ? (
                  <p>
                    Overall outcome:{' '}
                    {item.result.outcomeSuccess === null
                      ? 'inconclusive'
                      : String(item.result.outcomeSuccess)}
                    ; confidence: {item.result.outcomeConfidence}; process:{' '}
                    {item.result.progressPerFrame.join(', ')}
                  </p>
                ) : (
                  <dl>
                    <dt>Object</dt>
                    <dd>{item.result.findings.object}</dd>
                    <dt>Pick from</dt>
                    <dd>{item.result.findings.pickFrom}</dd>
                    <dt>Grasp</dt>
                    <dd>{String(item.result.findings.graspSuccess)}</dd>
                    <dt>Place</dt>
                    <dd>{String(item.result.findings.placeSuccess)}</dd>
                    <dt>Movement quality</dt>
                    <dd>{item.result.findings.movementQuality}</dd>
                    <dt>Notes</dt>
                    <dd>{item.result.findings.notes}</dd>
                  </dl>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}

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

      <p className="text-muted-foreground text-xs">
        VLM (vision-language model) evaluates task outcomes. GVL uses shuffle-and-rank scoring; VOC
        (value-order correlation) summarizes whether progress increases in the expected order.
      </p>

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

      {cameras.length > 0 && (
        <fieldset className="space-y-1" disabled={busy}>
          <legend className="text-muted-foreground text-xs font-medium">Judge cameras</legend>
          <div className="flex flex-wrap gap-3">
            {cameras.map((camera) => (
              <label key={camera} className="flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  checked={judgeViews.includes(camera)}
                  disabled={judgeViews.length === 1 && judgeViews.includes(camera)}
                  onChange={(event) =>
                    setViewSelection({
                      datasetId,
                      views: event.target.checked
                        ? [...judgeViews, camera]
                        : judgeViews.filter((view) => view !== camera),
                    })
                  }
                />
                {camera}
              </label>
            ))}
          </div>
        </fieldset>
      )}

      {!readiness.ready && (
        <p role="status" className="text-muted-foreground text-xs">
          {readiness.reason}
        </p>
      )}
      {!viewsReady && <p role="status">Choose a judge camera.</p>}
      <div className="flex flex-wrap gap-2 pt-1">
        <Button
          type="button"
          size="sm"
          onClick={() => handleRun(false)}
          disabled={!enabled || busy || !readiness.ready || !viewsReady}
        >
          <Play className="mr-1 size-3" />
          {runMutation.isPending ? 'Running…' : result ? 'Re-evaluate' : 'Run judge'}
        </Button>
        {result && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => handleRun(true)}
            disabled={!enabled || busy || !readiness.ready || !viewsReady}
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
            disabled={
              !enabled ||
              busy ||
              !readiness.ready ||
              assessment?.applicability !== 'current' ||
              result.outcomeSuccess === null
            }
            title={
              result.outcomeSuccess === null
                ? 'Inconclusive assessment has no outcome label'
                : `Apply ${outcomeToLabel(result)} as this episode's label`
            }
          >
            <Tag className="mr-1 size-3" />
            Apply label
          </Button>
        )}
      </div>
    </section>
  )
})
