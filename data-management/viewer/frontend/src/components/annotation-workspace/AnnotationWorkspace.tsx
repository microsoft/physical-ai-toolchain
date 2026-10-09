import { AnnotationWorkspaceContent } from '@/components/annotation-workspace/AnnotationWorkspaceContent'
import { AnnotationWorkspaceEmptyState } from '@/components/annotation-workspace/AnnotationWorkspaceEmptyState'
import { DraftConflictDialog } from '@/components/annotation-workspace/DraftConflictDialog'
import { useAnnotationWorkspaceShell } from '@/components/annotation-workspace/useAnnotationWorkspaceShell'
import { Button } from '@/components/ui/button'

interface AnnotationWorkspaceProps {
  visible?: boolean
  diagnosticsVisible?: boolean
  canGoPreviousEpisode?: boolean
  onPreviousEpisode?: () => void
  canGoNextEpisode?: boolean
  onNextEpisode?: () => void
  onSaveAndNextEpisode?: () => void
}

/**
 * Unified annotation workspace integrating episode viewing, editing, and export.
 *
 * Uses native <video> for smooth playback and per-frame <img> for
 * frame-accurate scrubbing when paused.
 */
export function AnnotationWorkspace({
  visible = true,
  diagnosticsVisible,
  canGoPreviousEpisode = false,
  onPreviousEpisode,
  canGoNextEpisode = false,
  onNextEpisode,
  onSaveAndNextEpisode,
}: AnnotationWorkspaceProps) {
  const shell = useAnnotationWorkspaceShell({
    visible,
    diagnosticsVisible,
    canGoPreviousEpisode,
    onPreviousEpisode,
    canGoNextEpisode,
    onNextEpisode,
    onSaveAndNextEpisode,
  })

  if (!shell.currentDataset || !shell.currentEpisode) {
    return (
      <>
        <AnnotationWorkspaceEmptyState />
        <DraftConflictDialog />
      </>
    )
  }

  return (
    <div hidden={!visible} className="h-full">
      {shell.editPersistenceError && (
        <div
          role="alert"
          className="text-destructive flex flex-wrap items-center gap-2 px-4 py-2 text-sm"
        >
          <p>{shell.editPersistenceError}</p>
          {shell.resolveRecoveredEdits && (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={() => shell.resolveRecoveredEdits?.('keep')}
              >
                Keep recovered edits
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => shell.resolveRecoveredEdits?.('discard')}
              >
                Discard recovered edits
              </Button>
            </>
          )}
        </div>
      )}
      <AnnotationWorkspaceContent shell={shell} />
      <DraftConflictDialog />
    </div>
  )
}
