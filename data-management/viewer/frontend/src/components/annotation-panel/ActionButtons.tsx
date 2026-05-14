/**
 * Action buttons for the annotation panel workflow.
 */

import { AlertCircle, Flag, Loader2, Save, SkipForward } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface ActionButtonsProps {
  /** Whether a save is in progress */
  isSaving: boolean
  /** Whether there are unsaved changes */
  isDirty: boolean
  /** Callback to save and advance */
  onSaveAndAdvance: () => void
  /** Callback to skip without saving */
  onSkip: () => void
  /** Callback to flag for review */
  onFlagForReview: () => void
  /** Callback to save without advancing */
  onSave: () => void
  /** Additional CSS classes */
  className?: string
}

/**
 * Action button bar for annotation workflow.
 *
 * @example
 * ```tsx
 * <ActionButtons
 *   isSaving={isSaving}
 *   isDirty={isDirty}
 *   onSaveAndAdvance={saveAndAdvance}
 *   onSkip={skip}
 *   onFlagForReview={flagForReview}
 *   onSave={save}
 * />
 * ```
 */
export function ActionButtons({
  isSaving,
  isDirty,
  onSaveAndAdvance,
  onSkip,
  onFlagForReview,
  onSave,
  className,
}: ActionButtonsProps) {
  return (
    <div className={cn('flex flex-col gap-2', className)}>
      {/* Dirty indicator */}
      {isDirty && (
        <div className="flex items-center gap-2 rounded-sm bg-orange-50 px-2 py-1 text-xs text-orange-600">
          <AlertCircle className="h-3 w-3" />
          Unsaved changes
        </div>
      )}

      {/* Primary actions */}
      <div className="flex gap-2">
        <Button onClick={onSaveAndAdvance} disabled={isSaving} className="flex-1">
          {isSaving ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Saving...
            </>
          ) : (
            <>
              <Save className="mr-2 h-4 w-4" />
              Save & Next
              <kbd className="ml-2 text-xs opacity-60">↵</kbd>
            </>
          )}
        </Button>

        <Button variant="outline" onClick={onSkip} disabled={isSaving}>
          <SkipForward className="mr-2 h-4 w-4" />
          Skip
        </Button>
      </div>

      {/* Secondary actions */}
      <div className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={onSave}
          disabled={isSaving || !isDirty}
          className="flex-1"
        >
          <Save className="mr-2 h-4 w-4" />
          Save Only
          <kbd className="ml-2 text-xs opacity-60">Ctrl+S</kbd>
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={onFlagForReview}
          disabled={isSaving}
          className="flex-1"
        >
          <Flag className="mr-2 h-4 w-4" />
          Flag for Review
        </Button>
      </div>
    </div>
  )
}
