import { AnnotationWorkspaceContent } from '@/components/annotation-workspace/AnnotationWorkspaceContent'
import { AnnotationWorkspaceEmptyState } from '@/components/annotation-workspace/AnnotationWorkspaceEmptyState'
import { useAnnotationWorkspaceShell } from '@/components/annotation-workspace/useAnnotationWorkspaceShell'

interface AnnotationWorkspaceProps {
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
  diagnosticsVisible,
  canGoPreviousEpisode = false,
  onPreviousEpisode,
  canGoNextEpisode = false,
  onNextEpisode,
  onSaveAndNextEpisode,
}: AnnotationWorkspaceProps) {
  const shell = useAnnotationWorkspaceShell({
    diagnosticsVisible,
    canGoPreviousEpisode,
    onPreviousEpisode,
    canGoNextEpisode,
    onNextEpisode,
    onSaveAndNextEpisode,
  })

  if (!shell.currentDataset || !shell.currentEpisode) {
    return <AnnotationWorkspaceEmptyState />
  }

  return <AnnotationWorkspaceContent shell={shell} />
}
