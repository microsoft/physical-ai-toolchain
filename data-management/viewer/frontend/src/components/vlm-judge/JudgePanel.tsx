/**
 * VLM-as-Judge panel.
 *
 * Surfaces the VLM judge harness directly inside the annotation workspace.
 * The component is self-disabling when the backend reports the judge is not
 * configured (``VLM_JUDGE_ENABLED=false``), so it can ship enabled by default.
 */

import { AlertCircle, CheckCircle2, Database, Play, RefreshCw, XCircle } from 'lucide-react'
import { memo, useCallback, useMemo } from 'react'

import { Button } from '@/components/ui/button'
import { useRunVlmJudge, useVlmJudgeStatus } from '@/hooks/use-vlm-judge'
import { cn } from '@/lib/utils'
import type { VlmJudgeResult } from '@/types'

export interface JudgePanelProps {
    datasetId: string
    episodeIndex: number
    /** Optional language instruction override; falls back to dataset metadata. */
    instruction?: string
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

export const JudgePanel = memo(function JudgePanel({
    datasetId,
    episodeIndex,
    instruction,
    className,
}: JudgePanelProps) {
    const status = useVlmJudgeStatus({ datasetId, episodeIndex })
    const runMutation = useRunVlmJudge()

    const result = useMemo(
        () => pickResult(status.data, runMutation.data),
        [status.data, runMutation.data],
    )
    const enabled = status.data?.enabled !== false
    const errorMessage = useMemo(() => {
        if (runMutation.error) return runMutation.error.message
        if (status.error) return (status.error as Error).message
        return null
    }, [runMutation.error, status.error])

    const handleRun = useCallback(
        (force: boolean) => {
            runMutation.mutate({
                datasetId,
                episodeIndex,
                options: {
                    instruction: instruction?.trim() || undefined,
                    force,
                },
            })
        },
        [runMutation, datasetId, episodeIndex, instruction],
    )

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

            {!result && (
                <p className="text-muted-foreground text-xs">
                    No judgment yet. Run the judge to score outcome and process reward.
                </p>
            )}

            {result && (
                <div className="space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                        <OutcomeBadge result={result} />
                        <span className="text-muted-foreground text-xs">
                            confidence {(result.outcomeConfidence * 100).toFixed(0)}% (
                            {result.outcomeNValidVotes} votes)
                        </span>
                        {result.cached && (
                            <span className="bg-muted text-muted-foreground rounded-full px-2 py-0.5 text-[10px] uppercase tracking-wide">
                                cached
                            </span>
                        )}
                    </div>

                    <div>
                        <p className="text-muted-foreground mb-1 text-xs font-medium">Process reward</p>
                        <ProgressSparkline values={result.progressPerFrame} />
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
                                            {m.evidence && (
                                                <p className="text-muted-foreground mt-0.5">{m.evidence}</p>
                                            )}
                                        </div>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    )}
                </div>
            )}

            <div className="flex flex-wrap gap-2 pt-1">
                <Button
                    type="button"
                    size="sm"
                    onClick={() => handleRun(false)}
                    disabled={runMutation.isPending}
                >
                    <Play className="mr-1 size-3" />
                    {result ? 'Re-evaluate' : 'Run judge'}
                </Button>
                {result && (
                    <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() => handleRun(true)}
                        disabled={runMutation.isPending}
                    >
                        <RefreshCw className="mr-1 size-3" />
                        Force fresh
                    </Button>
                )}
            </div>
        </section>
    )
})
