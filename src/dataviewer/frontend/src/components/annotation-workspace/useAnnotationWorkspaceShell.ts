import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { useSaveEpisodeLabels } from '@/hooks/use-labels'
import { isDiagnosticsEnabled, recordDiagnosticEvent } from '@/lib/playback-diagnostics'
import {
  useDatasetStore,
  useEditDirtyState,
  useEditStore,
  useEpisodeStore,
  useFrameInsertionState,
  usePlaybackControls,
  usePlaybackSettings,
  useViewerDisplay,
} from '@/stores'
import { getEffectiveFrameCount, getOriginalIndex } from '@/stores/edit-store'
import { useLabelStore } from '@/stores/label-store'
import { createDefaultSubtask } from '@/types/episode-edit'

import { useAnnotationWorkspaceDiagnostics } from './useAnnotationWorkspaceDiagnostics'
import { useAnnotationWorkspaceEpisodeActions } from './useAnnotationWorkspaceEpisodeActions'
import { useAnnotationWorkspaceMediaController } from './useAnnotationWorkspaceMediaController'
import { useAnnotationWorkspacePlayback } from './useAnnotationWorkspacePlayback'

const EMPTY_LABELS: string[] = []

interface UseAnnotationWorkspaceShellOptions {
  diagnosticsVisible?: boolean
  canGoPreviousEpisode?: boolean
  onPreviousEpisode?: () => void
  canGoNextEpisode?: boolean
  onNextEpisode?: () => void
  onSaveAndNextEpisode?: () => void
}

export function useAnnotationWorkspaceShell({
  diagnosticsVisible = isDiagnosticsEnabled(),
  canGoPreviousEpisode = false,
  onPreviousEpisode,
  canGoNextEpisode = false,
  onNextEpisode,
  onSaveAndNextEpisode,
}: UseAnnotationWorkspaceShellOptions) {
  const [exportDialogOpen, setExportDialogOpen] = useState(false)
  const [activeTab, setActiveTab] = useState('trajectory')
  const seekVideoFrameRef = useRef(
    (frame: number, _range: [number, number] | null, _constrainToRange = true) => frame,
  )
  const resumePlaybackRef = useRef((_: number) => {})

  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const currentEpisode = useEpisodeStore((state) => state.currentEpisode)
  const labelDataLoaded = useLabelStore((state) => state.isLoaded)
  const availableLabels = useLabelStore((state) => state.availableLabels)
  const episodeLabels = useLabelStore((state) => state.episodeLabels)
  const savedEpisodeLabels = useLabelStore((state) => state.savedEpisodeLabels)
  const setEpisodeLabelsInStore = useLabelStore((state) => state.setEpisodeLabels)
  const removedFrames = useEditStore((state) => state.removedFrames)
  const initializeEdit = useEditStore((state) => state.initializeEdit)
  const clearTransforms = useEditStore((state) => state.clearTransforms)
  const saveEpisodeDraft = useEditStore((state) => state.saveEpisodeDraft)
  const editDatasetId = useEditStore((state) => state.datasetId)
  const editEpisodeIndex = useEditStore((state) => state.episodeIndex)
  const subtasks = useEditStore((state) => state.subtasks)
  const addSubtask = useEditStore((state) => state.addSubtask)
  const globalTransform = useEditStore((state) => state.globalTransform)
  const { insertedFrames } = useFrameInsertionState()
  const { isDirty: hasEdits, resetEdits } = useEditDirtyState()
  const { currentFrame, isPlaying, playbackSpeed, setCurrentFrame, togglePlayback, setPlaybackSpeed } = usePlaybackControls()
  const { displayAdjustment, isActive: displayActive } = useViewerDisplay()
  const { autoPlay, autoLoop, setAutoPlay, setAutoLoop } = usePlaybackSettings()
  const saveEpisodeLabels = useSaveEpisodeLabels()

  const currentEpisodeLabels = useMemo(() => {
    if (!currentEpisode) {
      return EMPTY_LABELS
    }

    return episodeLabels[currentEpisode.meta.index] ?? EMPTY_LABELS
  }, [currentEpisode, episodeLabels])

  const savedLabelsForCurrentEpisode = useMemo(() => {
    if (!currentEpisode) {
      return EMPTY_LABELS
    }

    return savedEpisodeLabels[currentEpisode.meta.index] ?? EMPTY_LABELS
  }, [currentEpisode, savedEpisodeLabels])

  const diagnosticsEnabled = diagnosticsVisible && isDiagnosticsEnabled()

  const {
    hasPendingEpisodeChanges,
    saveStatusMessage,
    handleResetAll,
    handleSaveAndNextEpisode,
  } = useAnnotationWorkspaceEpisodeActions({
    diagnosticsEnabled,
    currentDatasetId: currentDataset?.id ?? null,
    currentEpisodeIndex: currentEpisode?.meta.index ?? null,
    currentEpisodeLabels,
    savedLabelsForCurrentEpisode,
    availableLabels,
    labelDataLoaded,
    hasEdits,
    onResetEdits: resetEdits,
    onSetEpisodeLabels: setEpisodeLabelsInStore,
    onSaveEpisodeDraft: saveEpisodeDraft,
    onSaveEpisodeLabels: saveEpisodeLabels.mutateAsync,
    onRecordEvent: recordDiagnosticEvent,
    canGoNextEpisode,
    onAdvanceToNextEpisode: onSaveAndNextEpisode ?? onNextEpisode,
  })

  useEffect(() => {
    if (currentDataset && currentEpisode) {
      const newDatasetId = currentDataset.id
      const newEpisodeIndex = currentEpisode.meta.index

      if (editDatasetId !== newDatasetId || editEpisodeIndex !== newEpisodeIndex) {
        initializeEdit(newDatasetId, newEpisodeIndex)
      }
    }
  }, [currentDataset, currentEpisode, editDatasetId, editEpisodeIndex, initializeEdit])

  const originalFrameCount = useMemo(() => {
    if (currentEpisode?.meta.length) {
      return currentEpisode.meta.length
    }
    if (currentEpisode?.trajectoryData?.length) {
      return currentEpisode.trajectoryData.length
    }
    return 100
  }, [currentEpisode])

  const totalFrames = useMemo(
    () => getEffectiveFrameCount(originalFrameCount, insertedFrames, removedFrames),
    [insertedFrames, originalFrameCount, removedFrames],
  )

  const originalFrameIndex = useMemo(
    () => getOriginalIndex(currentFrame, insertedFrames, removedFrames),
    [currentFrame, insertedFrames, removedFrames],
  )

  const handleTabChange = useCallback((nextTab: string) => {
    setActiveTab(nextTab)
    recordDiagnosticEvent('workspace', 'tab-change', {
      previousTab: activeTab,
      nextTab,
    })

    if (nextTab === 'detection') {
      recordDiagnosticEvent('detection', 'tab-viewed', {
        previousTab: activeTab,
        episodeIndex: currentEpisode?.meta.index ?? null,
      })
    }
  }, [activeTab, currentEpisode?.meta.index])

  const playback = useAnnotationWorkspacePlayback({
    autoLoop,
    currentFrame,
    currentDatasetId: currentDataset?.id ?? null,
    currentEpisodeIndex: currentEpisode?.meta.index ?? null,
    isPlaying,
    subtasks,
    totalFrames,
    onSeekFrame: (frame, range, constrainToRange) => seekVideoFrameRef.current(frame, range, constrainToRange),
    onResumePlayback: (frame) => resumePlaybackRef.current(frame),
    onTogglePlayback: togglePlayback,
    onSetCurrentFrame: setCurrentFrame,
    onRecordEvent: recordDiagnosticEvent,
  })

  const media = useAnnotationWorkspaceMediaController({
    currentDataset,
    currentEpisode,
    currentFrame,
    totalFrames,
    originalFrameIndex,
    activePlaybackRange: playback.activePlaybackRange,
    playbackRangeStart: playback.playbackRangeStart,
    playbackRangeEnd: playback.playbackRangeEnd,
    isPlaying,
    playbackSpeed,
    autoPlay,
    autoLoop,
    shouldLoopPlaybackRange: playback.shouldLoopPlaybackRange,
    displayAdjustment,
    displayActive,
    globalTransform,
    insertedFrames,
    removedFrames,
    onSetCurrentFrame: setCurrentFrame,
    onTogglePlayback: togglePlayback,
    onSetFrameWithinPlaybackRange: playback.setFrameWithinPlaybackRange,
    onRecordEvent: recordDiagnosticEvent,
  })

  seekVideoFrameRef.current = media.seekVideoFrame
  resumePlaybackRef.current = media.handleResumePlayback

  const diagnostics = useAnnotationWorkspaceDiagnostics({
    diagnosticsVisible,
    activeTab,
    currentDatasetId: currentDataset?.id ?? null,
    currentEpisodeIndex: currentEpisode?.meta.index ?? null,
    currentFrame,
    totalFrames,
    isPlaying,
    selectedRange: playback.selectedRange,
    selectedSubtaskId: playback.selectedSubtaskId,
  })

  const handleCreateSubtaskFromSelection = useCallback((range: [number, number]) => {
    const nextSegment = createDefaultSubtask(range, subtasks)

    addSubtask(nextSegment)
    playback.handleCreateSubtaskFromRange(nextSegment)
  }, [addSubtask, playback, subtasks])

  useEffect(() => {
    if (!playback.selectedRange) {
      return
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return
      }

      playback.clearPlaybackSelection()
    }

    window.addEventListener('keydown', handleKeyDown)

    return () => {
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [playback])

  const handleOpenExportDialog = useCallback(() => {
    setExportDialogOpen(true)
    recordDiagnosticEvent('export', 'dialog-open', {
      activeTab,
      episodeIndex: currentEpisode?.meta.index ?? null,
    })
  }, [activeTab, currentEpisode?.meta.index])

  const handleResetAllClick = useCallback(() => {
    recordDiagnosticEvent('workspace', 'reset-all', {
      activeTab,
      episodeIndex: currentEpisode?.meta.index ?? null,
      hasPendingEpisodeChanges,
    })
    void handleResetAll()
  }, [activeTab, currentEpisode?.meta.index, handleResetAll, hasPendingEpisodeChanges])

  return {
    activeTab,
    autoLoop,
    autoPlay,
    canGoNextEpisode,
    canGoPreviousEpisode,
    clearTransforms,
    currentDataset,
    currentEpisode,
    currentFrame,
    diagnostics,
    diagnosticsEnabled,
    displayFilter: media.displayFilter,
    exportDialogOpen,
    frameImageUrl: media.frameImageUrl,
    globalTransform,
    handleCreateSubtaskFromSelection,
    handleLoadedMetadata: media.handleLoadedMetadata,
    handleOpenExportDialog,
    handleResetAllClick,
    handleSaveAndNextEpisode,
    handleTabChange,
    handleVideoEnded: media.handleVideoEnded,
    hasPendingEpisodeChanges,
    interpolatedImageUrl: media.interpolatedImageUrl,
    isInsertedFrame: media.isInsertedFrame,
    isPlaying,
    onNextEpisode,
    onPreviousEpisode,
    onSaveAndNextEpisode,
    playback,
    playbackSpeed,
    saveEpisodeLabels,
    saveStatusMessage,
    setActiveTab,
    setAutoLoop,
    setAutoPlay,
    setExportDialogOpen,
    setPlaybackSpeed,
    togglePlayback,
    totalFrames,
    videoRef: media.videoRef,
    videoSrc: media.videoSrc,
    canvasRef: media.canvasRef,
  }
}
