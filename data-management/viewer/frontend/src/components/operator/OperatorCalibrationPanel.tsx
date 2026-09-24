import { FileCheck2, ScanSearch } from 'lucide-react'

import { Button } from '@/components/ui/button'
import type { OperatorCalibrationReport } from '@/types'

import { redactOperatorText } from './operator-display'

interface OperatorCalibrationPanelProps {
  report: OperatorCalibrationReport | undefined
  error: string | null
  disabled: boolean
  onCheck: () => void
}

export function OperatorCalibrationPanel({
  report,
  error,
  disabled,
  onCheck,
}: OperatorCalibrationPanelProps) {
  return (
    <section className="space-y-3 border-b pb-5" aria-labelledby="operator-calibration-heading">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 id="operator-calibration-heading" className="text-sm font-semibold">
            Saved calibration
          </h3>
          <p className="text-muted-foreground mt-1 text-xs">
            Saved-file validation checks structure and joint ranges. It does not verify connected
            hardware.
          </p>
        </div>
        <FileCheck2 className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden="true" />
      </div>
      <Button type="button" variant="outline" size="sm" onClick={onCheck} disabled={disabled}>
        <ScanSearch className="h-4 w-4" aria-hidden="true" />
        Check saved calibration
      </Button>
      {error && (
        <p className="text-destructive text-xs" role="alert">
          {redactOperatorText(error)}
        </p>
      )}
      {!report && !error && (
        <p className="text-muted-foreground text-xs">Saved-file validation has not run.</p>
      )}
      {report && (
        <div className="space-y-2 text-xs" aria-live="polite">
          <p className={report.valid ? 'font-medium' : 'text-destructive font-medium'}>
            {report.valid ? 'Saved files valid' : 'Saved files need attention'}
          </p>
          <p className="text-muted-foreground">
            Hardware verification is still required during preflight before physical operation.
          </p>
          {report.arms.map((arm) => (
            <div key={arm.role} className="flex items-start justify-between gap-3 border-t pt-2">
              <div className="min-w-0">
                <p className="font-medium capitalize">{arm.role} configuration</p>
                {arm.issues.map((issue) => (
                  <p key={issue} className="text-destructive mt-1">
                    {redactOperatorText(issue)}
                  </p>
                ))}
              </div>
              <span className={arm.valid ? 'text-muted-foreground' : 'text-destructive'}>
                {arm.valid ? 'Valid' : 'Blocked'}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
