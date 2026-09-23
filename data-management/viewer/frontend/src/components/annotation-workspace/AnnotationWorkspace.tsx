import { QueryClientProvider } from '@tanstack/react-query'

import { AnnotationWorkspaceContent } from '@/components/annotation-workspace/AnnotationWorkspaceContent'
import { AnnotationWorkspaceEmptyState } from '@/components/annotation-workspace/AnnotationWorkspaceEmptyState'
import { DraftConflictDialog } from '@/components/annotation-workspace/DraftConflictDialog'
import { useAnnotationWorkspaceShell } from '@/components/annotation-workspace/useAnnotationWorkspaceShell'
import { TooltipProvider } from '@/components/ui/tooltip'
import { queryClient } from '@/lib/query-client'

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
function AnnotationWorkspaceContentRoot({
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
    return (
      <>
        <AnnotationWorkspaceEmptyState />
        <DraftConflictDialog />
      </>
    )
  }

  return (
    <>
      <AnnotationWorkspaceContent shell={shell} />
      <DraftConflictDialog />
    </>
  )
}

export function AnnotationWorkspace(props: AnnotationWorkspaceProps) {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <AnnotationWorkspaceContentRoot {...props} />
      </TooltipProvider>
    </QueryClientProvider>
  )
}
