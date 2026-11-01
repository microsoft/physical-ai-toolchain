/**
 * Displays the persisted per-episode analysis record (VLM-derived labels plus
 * a compact motion summary) stored beside the dataset. Auto-loads with the
 * dataset labels, so it reflects whatever was saved in
 * ``meta/episode_labels.json``.
 */

import { CheckCircle2, HelpCircle, XCircle } from 'lucide-react'
import { memo } from 'react'

import { Button } from '@/components/ui/button'
import { useSavedEpisodeAnalysis } from '@/hooks/use-labels'
import { cn } from '@/lib/utils'

export interface EpisodeAnalysisCardProps {
  episodeIndex: number
  className?: string
}

function Outcome({ value, testId }: { value?: boolean | null; testId: string }) {
  if (value === true) {
    return (
      <span
        data-testid={testId}
        className="inline-flex items-center gap-1 rounded-full border border-green-200 bg-green-50 px-2 py-0.5 text-xs font-medium text-green-800"
      >
        <CheckCircle2 className="size-3" /> Success
      </span>
    )
  }
  if (value === false) {
    return (
      <span
        data-testid={testId}
        className="inline-flex items-center gap-1 rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-xs font-medium text-red-800"
      >
        <XCircle className="size-3" /> Failed
      </span>
    )
  }
  return (
    <span
      data-testid={testId}
      className="text-muted-foreground inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium"
    >
      <HelpCircle className="size-3" /> Unknown
    </span>
  )
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-muted-foreground text-[11px] leading-tight">{label}</p>
      <p className="text-sm leading-snug">{value}</p>
    </div>
  )
}

export const EpisodeAnalysisCard = memo(function EpisodeAnalysisCard({
  episodeIndex,
  className,
}: EpisodeAnalysisCardProps) {
  const saved = useSavedEpisodeAnalysis(episodeIndex)
  const record = saved.record
  const contributions =
    saved.ledger?.contributions.filter(
      (item) => item.field.startsWith('analysis/') && !saved.ledger?.withdrawn.includes(item.id),
    ) ?? []
  const origins = [...new Set(contributions.map((item) => item.origin))]
  const hasTask =
    record &&
    [
      record.pickFrom,
      record.object,
      record.graspSuccess,
      record.placeSuccess,
      record.movementQuality,
      record.notes,
    ].some((value) => value != null)
  const hasMotion =
    record &&
    (record.motionScore != null ||
      !!record.motionFlags?.length ||
      record.smoothness != null ||
      record.efficiency != null ||
      record.jitter != null)

  return (
    <section
      aria-label="Saved task and motion evidence"
      className={cn('space-y-3 border-t pt-3 text-sm', className)}
    >
      {saved.error && (
        <div role="alert">
          Saved analysis refresh failed.{record && ' Previously loaded analysis remains visible.'}
          <Button variant="outline" size="sm" onClick={() => void saved.refetch()}>
            Retry analysis
          </Button>
        </div>
      )}
      {saved.isLoading && <p role="status">Loading saved analysis...</p>}
      {saved.replaced && (
        <p role="status">Machine findings from another source revision are excluded.</p>
      )}
      {record && (
        <p className="text-muted-foreground text-xs">
          Origin: {origins.length ? origins.join(', ') : 'legacy / unknown'}
          {contributions.some((item) => saved.ledger?.acceptances[item.id]?.length) &&
            '; accepted without changing authorship'}
        </p>
      )}
      <header className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-medium">Task-specific findings</h3>
        {record?.source && <span className="text-muted-foreground text-xs">{record.source}</span>}
      </header>

      {!record || !hasTask ? (
        <p className="text-muted-foreground text-xs">No saved task-specific findings.</p>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            {record.pickFrom && (
              <span className="bg-muted inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs">
                Picks from <strong className="font-semibold">{record.pickFrom}</strong>
              </span>
            )}
            <span className="text-muted-foreground text-xs">Grasp</span>
            <Outcome value={record.graspSuccess} testId="grasp-outcome" />
            <span className="text-muted-foreground text-xs">Place</span>
            <Outcome value={record.placeSuccess} testId="place-outcome" />
          </div>

          {record.object && <Field label="Object" value={record.object} />}
          {record.movementQuality && (
            <Field label="Movement quality" value={record.movementQuality} />
          )}
          {record.notes && <Field label="Notes" value={record.notes} />}
        </div>
      )}
      <section aria-label="Motion analysis" className="space-y-2 border-t pt-3">
        <h3 className="text-sm font-medium">Motion analysis</h3>
        {!record || !hasMotion ? (
          <p className="text-muted-foreground text-xs">No saved motion analysis.</p>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            {record.motionScore != null && (
              <span className="text-muted-foreground text-xs">
                Motion score <strong className="text-foreground">{record.motionScore}</strong>/5
              </span>
            )}
            {record.motionFlags?.map((flag) => (
              <span
                key={flag}
                className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] text-amber-800"
              >
                {flag}
              </span>
            ))}
            {record.smoothness != null && (
              <Field label="Smoothness" value={String(record.smoothness)} />
            )}
            {record.efficiency != null && (
              <Field label="Efficiency" value={String(record.efficiency)} />
            )}
            {record.jitter != null && <Field label="Jitter" value={String(record.jitter)} />}
          </div>
        )}
      </section>
    </section>
  )
})
