/**
 * Individual marker on the timeline representing an anomaly.
 *
 * Performance: Wrapped in React.memo to prevent re-renders when sibling markers change.
 */

import { memo, useCallback, useState } from 'react'

import { cn } from '@/lib/utils'
import type { Anomaly } from '@/types'

interface TimelineMarkerProps {
  /** Anomaly data */
  anomaly: Anomaly
  /** Position as percentage (0-100) */
  position: number
  /** Click handler */
  onClick: () => void
}

/**
 * Clickable marker on the timeline showing an anomaly.
 *
 * Performance: Memoized to prevent re-renders during timeline interactions.
 */
export const TimelineMarker = memo(function TimelineMarker({
  anomaly,
  position,
  onClick,
}: TimelineMarkerProps) {
  const [showTooltip, setShowTooltip] = useState(false)

  const handleMouseEnter = useCallback(() => setShowTooltip(true), [])
  const handleMouseLeave = useCallback(() => setShowTooltip(false), [])

  // Severity colors
  const severityColors = {
    low: 'bg-yellow-500',
    medium: 'bg-orange-500',
    high: 'bg-red-500',
  }

  // Severity border colors for verified markers
  const severityBorderColors = {
    low: 'border-yellow-600',
    medium: 'border-orange-600',
    high: 'border-red-600',
  }

  return (
    <div className="absolute top-1/2 z-20 -translate-y-1/2" style={{ left: `${position}%` }}>
      <button
        onClick={onClick}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        className={cn(
          '-ml-1.5 h-3 w-3 cursor-pointer rounded-full transition-transform hover:scale-125',
          severityColors[anomaly.severity],
          anomaly.verified && `border-2 ${severityBorderColors[anomaly.severity]}`,
          anomaly.autoDetected && 'ring-1 ring-white/50',
        )}
        title={anomaly.description}
      />

      {/* Tooltip */}
      {showTooltip && (
        <div className="absolute bottom-full left-1/2 z-30 mb-2 -translate-x-1/2">
          <div className="min-w-[150px] rounded-md border bg-popover p-2 text-xs shadow-lg">
            <div className="font-medium">{anomaly.type}</div>
            <div className="text-muted-foreground">{anomaly.description}</div>
            <div className="mt-1 flex gap-2">
              <span
                className={cn(
                  'rounded px-1',
                  anomaly.severity === 'high'
                    ? 'bg-red-100 text-red-700'
                    : anomaly.severity === 'medium'
                      ? 'bg-orange-100 text-orange-700'
                      : 'bg-yellow-100 text-yellow-700',
                )}
              >
                {anomaly.severity}
              </span>
              {anomaly.verified && (
                <span className="rounded bg-green-100 px-1 text-green-700">verified</span>
              )}
              {anomaly.autoDetected && (
                <span className="rounded bg-blue-100 px-1 text-blue-700">auto</span>
              )}
            </div>
            <div className="mt-1 text-muted-foreground">
              Frames {anomaly.frameRange[0]}-{anomaly.frameRange[1]}
            </div>
          </div>
        </div>
      )}
    </div>
  )
})
