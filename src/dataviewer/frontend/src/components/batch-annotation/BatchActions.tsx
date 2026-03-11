/**
 * Batch action toolbar for applying annotations to selected episodes.
 */

import {
  AlertTriangle,
  Check,
  CheckSquare,
  Loader2,
  Square,
  Star,
  X,
} from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
// Utility import removed - using direct class names
import type { TaskCompletenessRating } from '@/types';

interface BatchActionsProps {
  /** Number of selected episodes */
  selectedCount: number;
  /** Total number of episodes */
  totalCount: number;
  /** Whether a batch operation is in progress */
  isProcessing: boolean;
  /** Progress percentage (0-100) */
  progress: number;
  /** Callback to select all */
  onSelectAll: () => void;
  /** Callback to clear selection */
  onClearSelection: () => void;
  /** Callback to apply rating to all selected */
  onApplyRating: (rating: TaskCompletenessRating) => void;
  /** Callback to apply quality score to all selected */
  onApplyQuality: (score: number) => void;
}

/**
 * Toolbar for batch annotation actions.
 *
 * @example
 * ```tsx
 * <BatchActions
 *   selectedCount={5}
 *   totalCount={100}
 *   isProcessing={false}
 *   progress={0}
 *   onSelectAll={handleSelectAll}
 *   onClearSelection={handleClear}
 *   onApplyRating={handleApplyRating}
 *   onApplyQuality={handleApplyQuality}
 * />
 * ```
 */
export function BatchActions({
  selectedCount,
  totalCount,
  isProcessing,
  progress,
  onSelectAll,
  onClearSelection,
  onApplyRating,
  onApplyQuality,
}: BatchActionsProps) {
  const [showConfirm, setShowConfirm] = useState<{
    action: () => void;
    label: string;
  } | null>(null);

  const handleConfirmedAction = (action: () => void, label: string) => {
    if (selectedCount > 10) {
      setShowConfirm({ action, label });
    } else {
      action();
    }
  };

  const confirmAction = () => {
    showConfirm?.action();
    setShowConfirm(null);
  };

  const hasSelection = selectedCount > 0;

  return (
    <Card className="sticky top-0 z-20">
      <CardContent className="p-4">
        <div className="flex items-center gap-4 flex-wrap">
          {/* Selection controls */}
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={onSelectAll}
              disabled={isProcessing}
            >
              <CheckSquare className="h-4 w-4 mr-2" />
              Select All
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onClearSelection}
              disabled={!hasSelection || isProcessing}
            >
              <Square className="h-4 w-4 mr-2" />
              Clear
            </Button>
          </div>

          {/* Selection count */}
          <div className="text-sm text-muted-foreground">
            {selectedCount} of {totalCount} selected
          </div>

          {/* Separator */}
          <div className="h-6 w-px bg-border" />

          {/* Quick rating actions */}
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">Apply to selected:</span>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                handleConfirmedAction(
                  () => onApplyRating('success'),
                  `Mark ${selectedCount} episodes as Success`
                )
              }
              disabled={!hasSelection || isProcessing}
            >
              <Check className="h-4 w-4 mr-1 text-green-500" />
              Success
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                handleConfirmedAction(
                  () => onApplyRating('partial'),
                  `Mark ${selectedCount} episodes as Partial`
                )
              }
              disabled={!hasSelection || isProcessing}
            >
              <Star className="h-4 w-4 mr-1 text-yellow-500" />
              Partial
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                handleConfirmedAction(
                  () => onApplyRating('failure'),
                  `Mark ${selectedCount} episodes as Failure`
                )
              }
              disabled={!hasSelection || isProcessing}
            >
              <AlertTriangle className="h-4 w-4 mr-1 text-red-500" />
              Failure
            </Button>
          </div>

          {/* Quality rating */}
          <div className="flex items-center gap-1">
            {[1, 2, 3, 4, 5].map((score) => (
              <Button
                key={score}
                variant="outline"
                size="sm"
                className="w-8 h-8 p-0"
                onClick={() =>
                  handleConfirmedAction(
                    () => onApplyQuality(score),
                    `Set quality to ${score} stars for ${selectedCount} episodes`
                  )
                }
                disabled={!hasSelection || isProcessing}
              >
                {score}★
              </Button>
            ))}
          </div>
        </div>

        {/* Progress bar */}
        {isProcessing && (
          <div className="mt-3">
            <div className="flex items-center gap-2 mb-1">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span className="text-sm">Processing batch...</span>
              <span className="text-sm text-muted-foreground">{progress}%</span>
            </div>
            <div className="h-2 bg-muted rounded-full overflow-hidden">
              <div
                className="h-full bg-primary transition-all duration-300"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>
        )}

        {/* Confirmation dialog */}
        {showConfirm && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
            <Card className="w-full max-w-md">
              <CardContent className="p-6">
                <h3 className="text-lg font-semibold mb-2">Confirm Batch Action</h3>
                <p className="text-muted-foreground mb-4">{showConfirm.label}?</p>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    onClick={() => setShowConfirm(null)}
                    className="flex-1"
                  >
                    <X className="h-4 w-4 mr-2" />
                    Cancel
                  </Button>
                  <Button onClick={confirmAction} className="flex-1">
                    <Check className="h-4 w-4 mr-2" />
                    Confirm
                  </Button>
                </div>
              </CardContent>
            </Card>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
