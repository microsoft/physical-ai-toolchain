import { ScanSearch } from 'lucide-react'

import type { OperatorCalibrationReport } from '@/api/operator'
import { Button } from '@/components/ui/button'

interface OperatorCalibrationPanelProps {
  report: OperatorCalibrationReport | undefined
  isPending: boolean
  error: string | null
  disabled: boolean
  onCheck: () => void
}

export function OperatorCalibrationPanel({
  report,
  isPending,
  error,
  disabled,
  onCheck,
}: OperatorCalibrationPanelProps) {
  return (
    <section
      className="mb-5 min-w-0 space-y-3 rounded-lg border p-3"
      aria-label="Calibration check"
    >
      <div>
        <h3 className="text-sm font-semibold">SO-101 calibration</h3>
        <p className="text-muted-foreground mt-1 text-xs">
          Check saved joint IDs, encoder ranges, and homing offsets. No devices are opened and no
          calibration values are changed.
        </p>
      </div>
      <Button type="button" variant="outline" onClick={onCheck} disabled={disabled || isPending}>
        <ScanSearch className="mr-2 h-4 w-4" />
        {isPending ? 'Checking calibration…' : 'Check calibration'}
      </Button>
      {error && (
        <p className="text-destructive text-sm" role="alert">
          {error}
        </p>
      )}
      {report && !isPending && !error && (
        <div className="min-w-0 space-y-3">
          <p
            className={
              report.valid ? 'text-sm font-medium' : 'text-destructive text-sm font-medium'
            }
            role="status"
          >
            {report.valid ? 'Saved calibration valid' : 'Calibration needs attention'}
          </p>
          <p className="text-muted-foreground text-xs">
            Hardware not checked. The worker verifies motor calibration before enabling torque. A
            physical calibration check still requires supervision. Run full preflight before
            starting a session.
          </p>
          {report.arms.map((arm) => (
            <div key={arm.role} className="min-w-0 space-y-2 border-t pt-2">
              <div className="flex items-center justify-between gap-2 text-sm">
                <h4 className="font-medium capitalize">{arm.role}</h4>
                <span className={arm.valid ? 'text-muted-foreground' : 'text-destructive'}>
                  {arm.valid ? 'File valid' : 'Blocked'}
                </span>
              </div>
              <p className="text-muted-foreground text-xs break-all">{arm.fileName}</p>
              {arm.issues.length > 0 && (
                <div className="space-y-1 text-xs">
                  <ul className="text-destructive list-inside list-disc">
                    {arm.issues.map((issue) => (
                      <li key={issue}>{issue}</li>
                    ))}
                  </ul>
                  <p>Restore a known calibration or use the official LeRobot calibration tools.</p>
                </div>
              )}
              {arm.joints.length > 0 && (
                <details className="min-w-0 text-xs">
                  <summary className="cursor-pointer font-medium">
                    Joint values (encoder ticks)
                  </summary>
                  <div className="mt-2 overflow-x-auto">
                    <table className="w-full text-left whitespace-nowrap">
                      <caption className="sr-only">
                        {arm.role} saved calibration in encoder ticks
                      </caption>
                      <thead>
                        <tr className="border-b">
                          <th className="p-1" scope="col">
                            Joint
                          </th>
                          <th className="p-1" scope="col">
                            ID
                          </th>
                          <th className="p-1" scope="col">
                            Offset
                          </th>
                          <th className="p-1" scope="col">
                            Range
                          </th>
                          <th className="p-1" scope="col">
                            Drive
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {arm.joints.map((joint) => (
                          <tr key={joint.name} className="border-b last:border-0">
                            <th className="p-1 font-normal" scope="row">
                              {joint.name.replaceAll('_', ' ')}
                            </th>
                            <td className="p-1 tabular-nums">{joint.id}</td>
                            <td className="p-1 tabular-nums">{joint.homingOffset}</td>
                            <td className="p-1 tabular-nums">
                              {joint.rangeMin}–{joint.rangeMax}
                            </td>
                            <td className="p-1 tabular-nums">{joint.driveMode}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
              )}
              {arm.sha256 && (
                <p className="text-muted-foreground text-xs break-all">SHA-256: {arm.sha256}</p>
              )}
            </div>
          ))}
          <p className="text-muted-foreground text-xs">
            Checked{' '}
            <time dateTime={report.checkedAt}>{new Date(report.checkedAt).toLocaleString()}</time>
          </p>
        </div>
      )}
    </section>
  )
}
