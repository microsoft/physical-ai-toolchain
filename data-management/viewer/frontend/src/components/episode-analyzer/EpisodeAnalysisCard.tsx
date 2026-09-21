/**
 * Displays the persisted per-episode analysis record (VLM-derived labels plus
 * a compact motion summary) stored beside the dataset. Auto-loads with the
 * dataset labels, so it reflects whatever was saved in
 * ``meta/episode_labels.json``.
 */

import { CheckCircle2, HelpCircle, Sparkles, XCircle } from 'lucide-react'
import { memo } from 'react'

import { cn } from '@/lib/utils'
import { useLabelStore } from '@/stores/label-store'

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
  const record = useLabelStore((state) => state.episodeAnalysis[episodeIndex])

  return (
    <section className={cn('space-y-3 rounded-md border p-3 text-sm', className)}>
      <header className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-medium">
          <Sparkles className="size-4" />
          Episode Analysis
        </h3>
        {record?.source && <span className="text-muted-foreground text-xs">{record.source}</span>}
      </header>

      {!record ? (
        <p className="text-muted-foreground text-xs">
          No saved analysis for this episode yet. Persist an analysis record through the API or the
          VLM dataset-labeling script to show it here.
        </p>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            {record.pick_from && (
              <span className="bg-muted inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs">
                Picks from <strong className="font-semibold">{record.pick_from}</strong>
              </span>
            )}
            <span className="text-muted-foreground text-xs">Grasp</span>
            <Outcome value={record.grasp_success} testId="grasp-outcome" />
            <span className="text-muted-foreground text-xs">Place</span>
            <Outcome value={record.place_success} testId="place-outcome" />
          </div>

          {record.object && <Field label="Object" value={record.object} />}
          {record.movement_quality && (
            <Field label="Movement quality" value={record.movement_quality} />
          )}
          {record.notes && <Field label="Notes" value={record.notes} />}

          {(record.motion_score != null ||
            (record.motion_flags && record.motion_flags.length > 0)) && (
            <div className="flex flex-wrap items-center gap-2 border-t pt-2">
              {record.motion_score != null && (
                <span className="text-muted-foreground text-xs">
                  Motion score <strong className="text-foreground">{record.motion_score}</strong>/5
                </span>
              )}
              {record.motion_flags?.map((flag) => (
                <span
                  key={flag}
                  className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] text-amber-800"
                >
                  {flag}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  )
})
