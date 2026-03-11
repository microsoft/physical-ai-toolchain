/**
 * Individual marker on the timeline representing an anomaly.
 *
 * Performance: Wrapped in React.memo to prevent re-renders when sibling markers change.
 */

import { memo, useCallback,useState } from 'react';

import { cn } from '@/lib/utils';
import type { Anomaly } from '@/types';

interface TimelineMarkerProps {
  /** Anomaly data */
  anomaly: Anomaly;
  /** Position as percentage (0-100) */
  position: number;
  /** Click handler */
  onClick: () => void;
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
  const [showTooltip, setShowTooltip] = useState(false);
  
  const handleMouseEnter = useCallback(() => setShowTooltip(true), []);
  const handleMouseLeave = useCallback(() => setShowTooltip(false), []);

  // Severity colors
  const severityColors = {
    low: 'bg-yellow-500',
    medium: 'bg-orange-500',
    high: 'bg-red-500',
  };

  // Severity border colors for verified markers
  const severityBorderColors = {
    low: 'border-yellow-600',
    medium: 'border-orange-600',
    high: 'border-red-600',
  };

  return (
    <div
      className="absolute top-1/2 -translate-y-1/2 z-20"
      style={{ left: `${position}%` }}
    >
      <button
        onClick={onClick}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        className={cn(
          'w-3 h-3 -ml-1.5 rounded-full cursor-pointer transition-transform hover:scale-125',
          severityColors[anomaly.severity],
          anomaly.verified && `border-2 ${severityBorderColors[anomaly.severity]}`,
          anomaly.autoDetected && 'ring-1 ring-white/50'
        )}
        title={anomaly.description}
      />

      {/* Tooltip */}
      {showTooltip && (
        <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 z-30">
          <div className="bg-popover border rounded-md shadow-lg p-2 min-w-[150px] text-xs">
            <div className="font-medium">{anomaly.type}</div>
            <div className="text-muted-foreground">{anomaly.description}</div>
            <div className="flex gap-2 mt-1">
              <span
                className={cn(
                  'px-1 rounded',
                  anomaly.severity === 'high'
                    ? 'bg-red-100 text-red-700'
                    : anomaly.severity === 'medium'
                      ? 'bg-orange-100 text-orange-700'
                      : 'bg-yellow-100 text-yellow-700'
                )}
              >
                {anomaly.severity}
              </span>
              {anomaly.verified && (
                <span className="px-1 bg-green-100 text-green-700 rounded">
                  verified
                </span>
              )}
              {anomaly.autoDetected && (
                <span className="px-1 bg-blue-100 text-blue-700 rounded">
                  auto
                </span>
              )}
            </div>
            <div className="text-muted-foreground mt-1">
              Frames {anomaly.frameRange[0]}-{anomaly.frameRange[1]}
            </div>
          </div>
        </div>
      )}
    </div>
  );
});
