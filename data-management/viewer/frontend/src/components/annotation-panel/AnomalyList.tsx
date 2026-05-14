/**
 * Anomaly list component displaying detected anomalies.
 */

import { AlertTriangle, CheckCircle, Trash2, Zap } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { getAnomalySeverityTone, getSemanticToneClasses } from '@/lib/semantic-state'
import { cn } from '@/lib/utils'
import type { Anomaly } from '@/types'

interface AnomalyListProps {
  /** List of anomalies */
  anomalies: Anomaly[]
  /** Callback to remove an anomaly */
  onRemove: (id: string) => void
  /** Callback to toggle verified status */
  onToggleVerified: (id: string) => void
  /** Callback when clicking an anomaly to seek */
  onSeek?: (frame: number) => void
}

/**
 * Displays a list of anomalies with severity and verification status.
 *
 * @example
 * ```tsx
 * <AnomalyList
 *   anomalies={annotation.anomalies}
 *   onRemove={handleRemove}
 *   onToggleVerified={handleVerify}
 *   onSeek={seekToFrame}
 * />
 * ```
 */
export function AnomalyList({ anomalies, onRemove, onToggleVerified, onSeek }: AnomalyListProps) {
  if (anomalies.length === 0) {
    return <p className="text-muted-foreground py-4 text-center text-sm">No anomalies detected</p>
  }

  return (
    <div className="max-h-48 space-y-2 overflow-y-auto">
      {anomalies.map((anomaly) => {
        const severityTone = getAnomalySeverityTone(anomaly.severity)

        return (
          <div
            key={anomaly.id}
            className={cn(
              'text-foreground flex items-start gap-2 rounded-md border p-2',
              getSemanticToneClasses('surface', severityTone),
            )}
          >
            <AlertTriangle
              className={cn(
                'mt-0.5 h-4 w-4 shrink-0',
                getSemanticToneClasses('icon', severityTone),
              )}
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium capitalize">
                  {anomaly.type.replace(/_/g, ' ')}
                </span>
                <Badge variant="status" tone={severityTone} className="text-xs">
                  {anomaly.severity}
                </Badge>
                {anomaly.autoDetected && (
                  <Badge variant="status" tone="info" className="gap-0.5 text-xs">
                    <Zap className="h-3 w-3" />
                    auto
                  </Badge>
                )}
                {anomaly.verified && (
                  <Badge variant="status" tone="success" className="gap-0.5 text-xs">
                    <CheckCircle className="h-3 w-3" />
                    verified
                  </Badge>
                )}
              </div>
              <p className="text-muted-foreground mt-0.5 truncate text-xs">{anomaly.description}</p>
              <button
                onClick={() => onSeek?.(anomaly.frameRange[0])}
                className="text-primary mt-0.5 text-xs hover:underline"
              >
                Frames {anomaly.frameRange[0]}-{anomaly.frameRange[1]}
              </button>
            </div>
            <div className="flex shrink-0 gap-1">
              {anomaly.autoDetected && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6"
                  onClick={() => onToggleVerified(anomaly.id)}
                  title={anomaly.verified ? 'Mark unverified' : 'Mark verified'}
                >
                  <CheckCircle
                    className={cn(
                      'h-3 w-3',
                      anomaly.verified
                        ? getSemanticToneClasses('icon', 'success')
                        : 'text-muted-foreground',
                    )}
                  />
                </Button>
              )}
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6"
                onClick={() => onRemove(anomaly.id)}
              >
                <Trash2 className="h-3 w-3" />
              </Button>
            </div>
          </div>
        )
      })}
    </div>
  )
}
