import { AnnotationWorkspaceContent } from '@/components/annotation-workspace/AnnotationWorkspaceContent'
import { AnnotationWorkspaceEmptyState } from '@/components/annotation-workspace/AnnotationWorkspaceEmptyState'
import { DraftConflictDialog } from '@/components/annotation-workspace/DraftConflictDialog'
import { useAnnotationWorkspaceShell } from '@/components/annotation-workspace/useAnnotationWorkspaceShell'

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
        <p role="alert" className="text-destructive px-4 py-2 text-sm">
          {shell.editPersistenceError}
        </p>
      )}
      <AnnotationWorkspaceContent shell={shell} />
      <DraftConflictDialog />
    </div>
  )
}
