/**
 * Issue list component for data quality issues.
 */

import { AlertCircle, AlertTriangle, Info,Trash2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { DataQualityIssue } from '@/types';

interface IssueListProps {
  /** List of issues */
  issues: DataQualityIssue[];
  /** Callback to remove an issue */
  onRemove: (index: number) => void;
  /** Callback when clicking an issue to seek */
  onSeek?: (frame: number) => void;
}

/**
 * Displays a list of data quality issues with severity indicators.
 *
 * @example
 * ```tsx
 * <IssueList
 *   issues={dataQuality.issues}
 *   onRemove={handleRemoveIssue}
 *   onSeek={seekToFrame}
 * />
 * ```
 */
export function IssueList({ issues, onRemove, onSeek }: IssueListProps) {
  if (issues.length === 0) {
    return (
      <p className="text-sm text-muted-foreground text-center py-4">
        No issues reported
      </p>
    );
  }

  const severityIcons = {
    critical: <AlertCircle className="h-4 w-4 text-red-500" />,
    major: <AlertTriangle className="h-4 w-4 text-orange-500" />,
    minor: <Info className="h-4 w-4 text-yellow-500" />,
  };

  const severityColors = {
    critical: 'border-red-200 bg-red-50',
    major: 'border-orange-200 bg-orange-50',
    minor: 'border-yellow-200 bg-yellow-50',
  };

  return (
    <div className="space-y-2 max-h-48 overflow-y-auto">
      {issues.map((issue, index) => (
        <div
          key={`${issue.type}-${issue.severity}-${issue.affectedFrames?.join('-') ?? 'na'}-${issue.notes ?? 'no-notes'}`}
          className={cn(
            'flex items-start gap-2 p-2 rounded-md border',
            severityColors[issue.severity]
          )}
        >
          {severityIcons[issue.severity]}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium capitalize">
                {issue.type.replace(/-/g, ' ')}
              </span>
              <span
                className={cn(
                  'text-xs px-1.5 rounded',
                  issue.severity === 'critical' && 'bg-red-200 text-red-800',
                  issue.severity === 'major' && 'bg-orange-200 text-orange-800',
                  issue.severity === 'minor' && 'bg-yellow-200 text-yellow-800'
                )}
              >
                {issue.severity}
              </span>
            </div>
            {issue.notes && (
              <p className="text-xs text-muted-foreground mt-0.5 truncate">
                {issue.notes}
              </p>
            )}
            {issue.affectedFrames && (
              <button
                onClick={() => onSeek?.(issue.affectedFrames![0])}
                className="text-xs text-primary hover:underline mt-0.5"
              >
                Frames {issue.affectedFrames[0]}-{issue.affectedFrames[1]}
              </button>
            )}
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 shrink-0"
            onClick={() => onRemove(index)}
          >
            <Trash2 className="h-3 w-3" />
          </Button>
        </div>
      ))}
    </div>
  );
}
