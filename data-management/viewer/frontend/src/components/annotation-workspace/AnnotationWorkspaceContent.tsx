import { PackageCheck } from 'lucide-react'

import {
  DataQualityWidget,
  LabelPanel,
  LanguageInstructionWidget,
  ObjectDetectionWidget,
} from '@/components/annotation-panel'
import { AnnotationWorkspaceAnalyzerTab } from '@/components/annotation-workspace/AnnotationWorkspaceAnalyzerTab'
import { AnnotationWorkspaceDiagnosticsPanel } from '@/components/annotation-workspace/AnnotationWorkspaceDiagnosticsPanel'
import { AnnotationWorkspaceEditToolsPanel } from '@/components/annotation-workspace/AnnotationWorkspaceEditToolsPanel'
import { AnnotationWorkspacePlaybackCard } from '@/components/annotation-workspace/AnnotationWorkspacePlaybackCard'
import { AnnotationWorkspaceSubtaskListCard } from '@/components/annotation-workspace/AnnotationWorkspaceSubtaskListCard'
import { AnnotationWorkspaceTopBar } from '@/components/annotation-workspace/AnnotationWorkspaceTopBar'
import { AnnotationWorkspaceTrajectoryTab } from '@/components/annotation-workspace/AnnotationWorkspaceTrajectoryTab'
import { ReviewQualityPanel } from '@/components/annotation-workspace/ReviewQualityPanel'
import { EpisodeAnalysisCard, MotionMetricsPanel } from '@/components/episode-analyzer'
import { ExportDialog } from '@/components/export'
import { ReleaseDialog } from '@/components/release'
import { ReleaseStatusBanner } from '@/components/release/ReleaseStatusBanner'
import { Tabs } from '@/components/ui/tabs'
import { JudgePanel } from '@/components/vlm-judge'
import { useCapabilities } from '@/hooks/use-datasets'
import { useReviewReasonDraft } from '@/hooks/use-review-reason-draft'
import {
  useCreateReviewDecision,
  useReviewDecision,
  useReviewQuality,
  useRunQualityReview,
} from '@/hooks/use-reviews'
import { useAnnotationStore, useEditStore } from '@/stores'

import type { useAnnotationWorkspaceShell } from './useAnnotationWorkspaceShell'

interface AnnotationWorkspaceContentProps {
  shell: ReturnType<typeof useAnnotationWorkspaceShell>
}

export function AnnotationWorkspaceContent({ shell }: AnnotationWorkspaceContentProps) {
  const currentDataset = shell.currentDataset
  const currentEpisode = shell.currentEpisode
  // Current (draft or saved) language instruction so the judge scores against
  // what the annotator sees in the Language Instruction widget; falls back to
  // dataset metadata on the backend when empty.
  const currentInstruction = useAnnotationStore(
    (state) => state.currentAnnotation?.languageInstruction?.instruction,
  )
  const currentAnnotation = useAnnotationStore((state) => state.currentAnnotation)
  const getEditOperations = useEditStore((state) => state.getEditOperations)
  const capabilities = useCapabilities(currentDataset?.id)
  const quality = useReviewQuality(currentDataset?.id ?? '', currentEpisode?.meta.index ?? -1)
  const decision = useReviewDecision(currentDataset?.id ?? '', currentEpisode?.meta.index ?? -1)
  const runQuality = useRunQualityReview()
  const createDecision = useCreateReviewDecision()
  const isReadOnly = Boolean(currentDataset?.isReadOnly)
  const reviewReasonDraft = useReviewReasonDraft(
    currentDataset?.id ?? '',
    currentEpisode?.meta.index ?? -1,
    currentAnnotation?.annotatorId ?? '',
  )

  if (!currentDataset || !currentEpisode) {
    return null
  }

  const trajectoryPlaybackCard = (
    <AnnotationWorkspacePlaybackCard
      compact
      canvasRef={shell.canvasRef}
      videoRef={shell.videoRef}
      videoSrc={shell.videoSrc}
      videoUrls={shell.videoUrls}
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

  const readOnlyNotice = (
    <p className="text-muted-foreground text-sm">
      This verified release is read-only. Return to{' '}
      {currentDataset.sourceDatasetId ?? 'the source dataset'} to annotate or edit.
    </p>
  )
  const trajectorySubtaskListCard = isReadOnly ? (
    readOnlyNotice
  ) : (
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

  const trajectoryLabelPanel = isReadOnly ? (
    readOnlyNotice
  ) : (
    <LabelPanel episodeIndex={currentEpisode.meta.index} />
  )
  const trajectoryJudgePanel = isReadOnly ? (
    readOnlyNotice
  ) : (
    <JudgePanel
      datasetId={currentDataset.id}
      episodeIndex={currentEpisode.meta.index}
      instruction={currentInstruction}
      totalEpisodes={currentDataset.totalEpisodes}
    />
  )
  const trajectoryLanguageInstructionPanel = isReadOnly ? (
    readOnlyNotice
  ) : (
    <LanguageInstructionWidget />
  )
  const trajectoryObjectDetectionPanel = isReadOnly ? readOnlyNotice : <ObjectDetectionWidget />
  const trajectoryEditToolsPanel = isReadOnly ? (
    readOnlyNotice
  ) : (
    <AnnotationWorkspaceEditToolsPanel
      onClearTransforms={shell.clearTransforms}
      canResetTransforms={Boolean(shell.globalTransform)}
    />
  )
  const trajectoryDataQualityPanel = isReadOnly ? readOnlyNotice : <DataQualityWidget embedded />
  const sourceFormat = capabilities.data?.isLerobotDataset
    ? 'lerobot'
    : capabilities.data?.hasHdf5Files
      ? 'hdf5'
      : null
  const trajectoryReviewQualityPanel = isReadOnly ? (
    readOnlyNotice
  ) : (
    <ReviewQualityPanel
      qualityReport={quality.data ?? null}
      qualityError={runQuality.error?.message ?? null}
      isRunningQuality={runQuality.isPending}
      isSubmittingDecision={createDecision.isPending}
      reasonCodes={reviewReasonDraft.reasonCodes}
      onReasonCodesChange={reviewReasonDraft.setReasonCodes}
      onRunQuality={() => {
        if (!currentAnnotation || !sourceFormat) {
          return
        }
        runQuality.mutate({
          datasetId: currentDataset.id,
          episodeIndex: currentEpisode.meta.index,
          actorId: currentAnnotation.annotatorId,
          sourceFormat,
          profile: {
            profileId: 'workspace-review',
            version: '1.0.0',
            fps: currentDataset.fps,
            timestampToleranceSeconds: 1 / currentDataset.fps / 2,
            requiredFeatures: Object.entries(currentDataset.features).map(([name, feature]) => ({
              name,
              dtype: feature.dtype,
              shape: feature.shape,
            })),
            optionalFeatures: [],
            requiredMetadataFiles: [],
            calibration: null,
            requireTaskLabel: true,
          },
        })
      }}
      onDecision={(decision, reasonCodes) => {
        const edits = getEditOperations()
        if (!currentAnnotation || !quality.data || !edits) {
          return
        }
        createDecision.mutate({
          datasetId: currentDataset.id,
          episodeIndex: currentEpisode.meta.index,
          actorId: currentAnnotation.annotatorId,
          annotation: currentAnnotation,
          edits,
          qualityReport: quality.data,
          decision,
          reasonCodes,
        })
      }}
    />
  )

  const analyzerMotionMetricsPanel = (
    <MotionMetricsPanel
      datasetId={currentDataset.id}
      episodeId={String(currentEpisode.meta.index)}
      positions={currentEpisode.trajectoryData?.map((point) => point.jointPositions)}
      timestamps={currentEpisode.trajectoryData?.map((point) => point.timestamp)}
      gripperStates={currentEpisode.trajectoryData?.map((point) => point.gripperState)}
    />
  )

  const episodeAnalysisCard = <EpisodeAnalysisCard episodeIndex={currentEpisode.meta.index} />

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
          onOpenReleaseDialog={shell.handleOpenReleaseDialog}
          canGoNextEpisode={shell.canGoNextEpisode}
          canSaveAndNextEpisode={
            Boolean(shell.onSaveAndNextEpisode) &&
            !shell.saveEpisodeLabels.isPending &&
            !shell.saveCurrentAnnotation.isPending
          }
          onSaveAndNextEpisode={() => void shell.handleSaveAndNextEpisode()}
          saveStatusMessage={shell.saveStatusMessage}
          isReadOnly={isReadOnly}
        />

        {isReadOnly && (
          <div className="bg-muted/50 flex items-center gap-2 border px-3 py-2 text-sm">
            <PackageCheck className="h-4 w-4" aria-hidden="true" />
            <strong>Viewing release {currentDataset.releaseId}</strong>
            <span className="text-muted-foreground">Verified and read-only</span>
          </div>
        )}

        <ReleaseStatusBanner
          datasetId={currentDataset.id}
          onViewStatus={shell.handleOpenReleaseDialog}
        />

        <AnnotationWorkspaceTrajectoryTab
          playbackCard={trajectoryPlaybackCard}
          subtaskListCard={trajectorySubtaskListCard}
          labelPanel={trajectoryLabelPanel}
          judgePanel={trajectoryJudgePanel}
          analysisCard={episodeAnalysisCard}
          languageInstructionPanel={trajectoryLanguageInstructionPanel}
          objectDetectionPanel={trajectoryObjectDetectionPanel}
          editToolsPanel={trajectoryEditToolsPanel}
          dataQualityPanel={trajectoryDataQualityPanel}
          reviewQualityPanel={trajectoryReviewQualityPanel}
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

        <AnnotationWorkspaceAnalyzerTab
          motionMetricsPanel={analyzerMotionMetricsPanel}
          judgePanel={trajectoryJudgePanel}
          analysisCard={episodeAnalysisCard}
          labelPanel={trajectoryLabelPanel}
          languageInstructionPanel={trajectoryLanguageInstructionPanel}
        />
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

      {!isReadOnly && (
        <ExportDialog
          open={shell.exportDialogOpen}
          onOpenChange={shell.setExportDialogOpen}
          datasetId={currentDataset.id}
          episodeIndices={[currentEpisode.meta.index]}
        />
      )}
      {!isReadOnly && (
        <ReleaseDialog
          open={shell.releaseDialogOpen}
          onOpenChange={shell.setReleaseDialogOpen}
          datasetId={currentDataset.id}
          episodeIndex={currentEpisode.meta.index}
          actorId={decision.data?.actorId ?? currentAnnotation?.annotatorId ?? ''}
          decision={decision.data ?? null}
        />
      )}
    </div>
  )
}
