import { LabelPanel, LanguageInstructionWidget } from '@/components/annotation-panel'
import { AnnotationWorkspaceDiagnosticsPanel } from '@/components/annotation-workspace/AnnotationWorkspaceDiagnosticsPanel'
import { AnnotationWorkspaceEditToolsPanel } from '@/components/annotation-workspace/AnnotationWorkspaceEditToolsPanel'
import { AnnotationWorkspacePlaybackCard } from '@/components/annotation-workspace/AnnotationWorkspacePlaybackCard'
import { AnnotationWorkspaceSubtaskListCard } from '@/components/annotation-workspace/AnnotationWorkspaceSubtaskListCard'
import { AnnotationWorkspaceTopBar } from '@/components/annotation-workspace/AnnotationWorkspaceTopBar'
import { AnnotationWorkspaceTrajectoryTab } from '@/components/annotation-workspace/AnnotationWorkspaceTrajectoryTab'
import { ExportDialog } from '@/components/export'
import { DetectionPanel } from '@/components/object-detection'
import { Tabs, TabsContent } from '@/components/ui/tabs'

import type { useAnnotationWorkspaceShell } from './useAnnotationWorkspaceShell'

interface AnnotationWorkspaceContentProps {
  shell: ReturnType<typeof useAnnotationWorkspaceShell>
}

export function AnnotationWorkspaceContent({ shell }: AnnotationWorkspaceContentProps) {
  const currentDataset = shell.currentDataset
  const currentEpisode = shell.currentEpisode

  if (!currentDataset || !currentEpisode) {
    return null
  }

  const trajectoryPlaybackCard = (
    <AnnotationWorkspacePlaybackCard
      compact
      canvasRef={shell.canvasRef}
      videoRef={shell.videoRef}
      videoSrc={shell.videoSrc}
      onVideoEnded={shell.handleVideoEnded}
      onLoadedMetadata={shell.handleLoadedMetadata}
      displayFilter={shell.displayFilter}
      isInsertedFrame={shell.isInsertedFrame}
      interpolatedImageUrl={shell.interpolatedImageUrl}
      currentFrame={shell.currentFrame}
      totalFrames={shell.totalFrames}
      resizeOutput={shell.globalTransform?.resize ?? null}
      frameImageUrl={shell.frameImageUrl}
      cameras={shell.cameras}
      selectedCamera={shell.cameraName}
      onSelectCamera={shell.setCameraName}
      isPlaying={shell.isPlaying}
      onTogglePlayback={shell.togglePlayback}
      onStepFrame={shell.playback.stepFrame}
      playbackSpeed={shell.playbackSpeed}
      onSetPlaybackSpeed={shell.setPlaybackSpeed}
      autoPlay={shell.autoPlay}
      onSetAutoPlay={shell.setAutoPlay}
      autoLoop={shell.autoLoop}
      onSetAutoLoop={shell.setAutoLoop}
      playbackRangeStart={shell.playback.playbackRangeStart}
      playbackRangeEnd={shell.playback.playbackRangeEnd}
      onSetFrameWithinPlaybackRange={shell.playback.setFrameWithinPlaybackRange}
      playbackRangeHighlight={shell.playback.playbackRangeHighlight}
      playbackRangeLabel={shell.playback.playbackRangeLabel}
    />
  )

  const trajectorySubtaskListCard = (
    <AnnotationWorkspaceSubtaskListCard
      compact
      selectedSubtaskId={shell.playback.selectedSubtaskId}
      onSelectionChange={shell.playback.handleSubtaskSelectionChange}
      draftRange={shell.playback.selectedRange}
      maxFrame={Math.max(shell.totalFrames - 1, 0)}
      onDraftRangeChange={shell.playback.handleDraftRangeChange}
      onCreateSubtaskFromRange={shell.handleCreateSubtaskFromSelection}
    />
  )

  const trajectoryLabelPanel = <LabelPanel episodeIndex={currentEpisode.meta.index} />
  const trajectoryLanguageInstructionPanel = <LanguageInstructionWidget />
  const trajectoryEditToolsPanel = (
    <AnnotationWorkspaceEditToolsPanel
      onClearTransforms={shell.clearTransforms}
      canResetTransforms={Boolean(shell.globalTransform)}
    />
  )

  return (
    <div className="flex h-full flex-col gap-2.5 overflow-y-auto px-3 py-2">
      <Tabs
        value={shell.activeTab}
        onValueChange={shell.handleTabChange}
        className="flex flex-1 flex-col"
      >
        <AnnotationWorkspaceTopBar
          episodeIndex={currentEpisode.meta.index}
          canGoPreviousEpisode={shell.canGoPreviousEpisode}
          onPreviousEpisode={shell.onPreviousEpisode}
          hasPendingEpisodeChanges={shell.hasPendingEpisodeChanges}
          onResetAllClick={shell.handleResetAllClick}
          onOpenExportDialog={shell.handleOpenExportDialog}
          canGoNextEpisode={shell.canGoNextEpisode}
          canSaveAndNextEpisode={
            Boolean(shell.onSaveAndNextEpisode) && !shell.saveEpisodeLabels.isPending
          }
          onSaveAndNextEpisode={() => void shell.handleSaveAndNextEpisode()}
          saveStatusMessage={shell.saveStatusMessage}
        />

        <AnnotationWorkspaceTrajectoryTab
          playbackCard={trajectoryPlaybackCard}
          subtaskListCard={trajectorySubtaskListCard}
          labelPanel={trajectoryLabelPanel}
          languageInstructionPanel={trajectoryLanguageInstructionPanel}
          editToolsPanel={trajectoryEditToolsPanel}
          selectedRange={shell.playback.selectedRange}
          selectedSubtaskId={shell.playback.selectedSubtaskId}
          onClearPlaybackSelection={shell.playback.clearPlaybackSelection}
          onDraftRangeChange={shell.playback.handleDraftRangeChange}
          onCreateSubtaskFromRange={shell.handleCreateSubtaskFromSelection}
          onGraphSeek={shell.playback.handleGraphSeek}
          onSelectionStart={shell.playback.handleSelectionStart}
          onSelectionComplete={shell.playback.handleSelectionComplete}
          totalFrames={shell.totalFrames}
          onSubtaskSelectionChange={shell.playback.handleSubtaskSelectionChange}
        />

        <TabsContent value="detection" className="mt-2.5 min-h-0 flex-1">
          <DetectionPanel />
        </TabsContent>
      </Tabs>

      {shell.diagnosticsEnabled && (
        <AnnotationWorkspaceDiagnosticsPanel
          diagnosticsStateSummary={shell.diagnostics.diagnosticsStateSummary}
          availableDiagnosticsChannels={shell.diagnostics.availableDiagnosticsChannels}
          selectedDiagnosticsChannel={shell.diagnostics.selectedDiagnosticsChannel}
          onSelectedDiagnosticsChannelChange={shell.diagnostics.setSelectedDiagnosticsChannel}
          onClearVisibleDiagnostics={shell.diagnostics.handleClearVisibleDiagnostics}
          onCopyDiagnostics={() => void shell.diagnostics.handleCopyDiagnostics()}
          onDownloadDiagnostics={shell.diagnostics.handleDownloadDiagnostics}
          diagnosticsClipboardStatus={shell.diagnostics.diagnosticsClipboardStatus}
          recentDiagnosticEvents={shell.diagnostics.recentDiagnosticEvents}
          playbackRangeStart={shell.playback.playbackRangeStart}
          playbackRangeEnd={shell.playback.playbackRangeEnd}
          shouldLoopPlaybackRange={shell.playback.shouldLoopPlaybackRange}
        />
      )}

      <ExportDialog
        open={shell.exportDialogOpen}
        onOpenChange={shell.setExportDialogOpen}
        datasetId={currentDataset.id}
        episodeIndices={[currentEpisode.meta.index]}
      />
    </div>
  )
}
