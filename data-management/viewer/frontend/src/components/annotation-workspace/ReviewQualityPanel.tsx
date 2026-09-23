import { AlertTriangle, Check, Play, X } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import type { QualityReport, ReviewDecisionValue } from '@/types'

const REASON_OPTIONS = [
  { code: 'evidence-reviewed', label: 'Evidence reviewed' },
  { code: 'needs-recapture', label: 'Needs recapture' },
  { code: 'annotation-correction', label: 'Annotation correction' },
] as const

interface ReviewQualityPanelProps {
  qualityReport: QualityReport | null
  qualityError: string | null
  isRunningQuality: boolean
  isSubmittingDecision: boolean
  onRunQuality: () => void
  onDecision: (decision: ReviewDecisionValue, reasonCodes: string[]) => void
  reasonCodes?: string[]
  onReasonCodesChange?: (reasonCodes: string[]) => void
}

export function ReviewQualityPanel({
  qualityReport,
  qualityError,
  isRunningQuality,
  isSubmittingDecision,
  onRunQuality,
  onDecision,
  reasonCodes: controlledReasonCodes,
  onReasonCodesChange,
}: ReviewQualityPanelProps) {
  const [localReasonCodes, setLocalReasonCodes] = useState<string[]>([])
  const reasonCodes = controlledReasonCodes ?? localReasonCodes
  const setReasonCodes = onReasonCodesChange ?? setLocalReasonCodes
  const blockingFailures = qualityReport
    ? [...qualityReport.episodeChecks, ...qualityReport.packageChecks].filter(
        (check) => check.required && check.outcome === 'fail',
      )
    : []

  const toggleReason = (code: string, checked: boolean) => {
    setReasonCodes(checked ? [...reasonCodes, code] : reasonCodes.filter((value) => value !== code))
  }

  return (
    <section aria-labelledby="review-quality-heading" className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <h3 id="review-quality-heading" className="text-sm font-medium">
            Review decision
          </h3>
          {qualityReport && (
            <p className="text-muted-foreground truncate text-xs">
              Quality run <span className="font-mono">{qualityReport.runId}</span>
            </p>
          )}
        </div>
        <Button size="sm" variant="outline" onClick={onRunQuality} disabled={isRunningQuality}>
          <Play className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
          {isRunningQuality ? 'Running...' : 'Run quality'}
        </Button>
      </div>

      {qualityError && (
        <p role="alert" className="text-destructive text-xs">
          Quality run failed: {qualityError}
        </p>
      )}

      {qualityReport && (
        <div className="space-y-1.5" aria-live="polite">
          {blockingFailures.length === 0 ? (
            <div className="flex items-center gap-2 text-xs">
              <Check className="h-4 w-4 text-emerald-600" aria-hidden="true" />
              <span>All required checks passed</span>
            </div>
          ) : (
            blockingFailures.map((failure) => (
              <div
                key={failure.checkId}
                className="border-destructive/40 bg-destructive/5 flex items-start gap-2 rounded-md border px-2 py-1.5 text-xs"
              >
                <AlertTriangle
                  className="text-destructive mt-0.5 h-3.5 w-3.5 shrink-0"
                  aria-label="Blocking required failure"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center justify-between gap-1">
                    <span className="font-mono break-all">{failure.checkId}</span>
                    <span className="font-medium">Blocking</span>
                  </div>
                  {failure.reasonCodes.length > 0 && (
                    <p className="text-muted-foreground break-words">
                      {failure.reasonCodes.join(', ')}
                    </p>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      )}

      <fieldset className="space-y-1.5" disabled={!qualityReport || isSubmittingDecision}>
        <legend className="text-xs font-medium">Reason codes</legend>
        {!qualityReport && (
          <p className="text-muted-foreground text-xs">
            Run quality to enable reason codes and review decisions.
          </p>
        )}
        <div className="grid gap-1.5 sm:grid-cols-2">
          {REASON_OPTIONS.map((reason) => (
            <label key={reason.code} className="flex items-center gap-2 text-xs">
              <Checkbox
                aria-label={reason.label}
                checked={reasonCodes.includes(reason.code)}
                onCheckedChange={(checked) => toggleReason(reason.code, checked === true)}
              />
              {reason.label}
            </label>
          ))}
        </div>
      </fieldset>

      <div className="grid grid-cols-2 gap-2">
        <Button
          size="sm"
          disabled={!qualityReport || reasonCodes.length === 0 || isSubmittingDecision}
          onClick={() => onDecision('accept', reasonCodes)}
        >
          <Check className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
          Accept episode
        </Button>
        <Button
          size="sm"
          variant="destructive"
          disabled={!qualityReport || reasonCodes.length === 0 || isSubmittingDecision}
          onClick={() => onDecision('reject', reasonCodes)}
        >
          <X className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
          Reject episode
        </Button>
      </div>
    </section>
  )
}
