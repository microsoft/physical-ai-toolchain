import {
  Activity,
  Download,
  Gauge,
  PackageCheck,
  RotateCcw,
  SkipBack,
  SkipForward,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

interface AnnotationWorkspaceTopBarProps {
  episodeIndex: number
  canGoPreviousEpisode: boolean
  onPreviousEpisode?: () => void
  hasPendingEpisodeChanges: boolean
  onResetAllClick: () => void
  onOpenExportDialog: () => void
  onOpenReleaseDialog: () => void
  canGoNextEpisode: boolean
  canSaveAndNextEpisode: boolean
  onSaveAndNextEpisode: () => void
  saveStatusMessage: string | null
  isReadOnly?: boolean
}

export function AnnotationWorkspaceTopBar({
  episodeIndex,
  canGoPreviousEpisode,
  onPreviousEpisode,
  hasPendingEpisodeChanges,
  onResetAllClick,
  onOpenExportDialog,
  onOpenReleaseDialog,
  canGoNextEpisode,
  canSaveAndNextEpisode,
  onSaveAndNextEpisode,
  saveStatusMessage,
  isReadOnly = false,
}: AnnotationWorkspaceTopBarProps) {
  return (
    <div
      className="flex flex-col gap-2.5 xl:grid xl:grid-cols-[minmax(0,1fr)_auto] xl:items-start xl:gap-3"
      data-testid="workspace-top-bar"
    >
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-3 xl:contents">
        <div className="flex min-w-0 items-center gap-2">
          <h2 className="text-lg leading-none font-semibold">Episode {episodeIndex}</h2>
        </div>
        <div
          className="flex min-w-0 flex-col gap-1 xl:row-span-2 xl:items-end xl:justify-self-end"
          data-testid="workspace-header-actions"
        >
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              variant="outline"
              size="icon"
              onClick={onPreviousEpisode}
              disabled={!canGoPreviousEpisode || !onPreviousEpisode}
              aria-label="Previous Episode"
              title="Previous Episode"
            >
              <SkipBack className="h-4 w-4" />
            </Button>
            {!isReadOnly && (
              <>
                <Button
                  variant="outline"
                  onClick={onResetAllClick}
                  disabled={!hasPendingEpisodeChanges}
                >
                  <RotateCcw className="mr-2 h-4 w-4" />
                  Reset All
                </Button>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button variant="outline" onClick={onOpenExportDialog} aria-label="Export">
                      <Download className="mr-2 h-4 w-4" />
                      Export Copy
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-72">
                    Create a separate mutable copy with selected edits. This does not approve or
                    release the episode.
                  </TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button variant="outline" onClick={onOpenReleaseDialog} aria-label="Release">
                      <PackageCheck className="mr-2 h-4 w-4" />
                      Create Release
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-72">
                    Publish an accepted episode as an immutable, verified LeRobot package.
                  </TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span>
                      <Button
                        onClick={onSaveAndNextEpisode}
                        disabled={!canGoNextEpisode || !canSaveAndNextEpisode}
                        aria-label="Save & Next Episode"
                      >
                        <SkipForward className="mr-2 h-4 w-4" />
                        Save & Next
                      </Button>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-72">
                    Save annotations, quality settings, labels, and edits, then open the next
                    episode.
                  </TooltipContent>
                </Tooltip>
              </>
            )}
          </div>
          <div className="min-h-[1rem] xl:text-right" data-testid="workspace-save-status-slot">
            {saveStatusMessage && (
              <p data-testid="workspace-save-status" className="text-muted-foreground text-xs">
                {saveStatusMessage}
              </p>
            )}
          </div>
        </div>
      </div>
      <TabsList className="h-auto w-full justify-start gap-1 overflow-x-auto md:flex-wrap xl:overflow-visible">
        <TabsTrigger value="trajectory" className="gap-2">
          <Activity className="h-4 w-4" />
          Trajectory Viewer
        </TabsTrigger>
        <TabsTrigger value="analyzer" className="gap-2">
          <Gauge className="h-4 w-4" />
          Episode Analyzer
        </TabsTrigger>
      </TabsList>
    </div>
  )
}
