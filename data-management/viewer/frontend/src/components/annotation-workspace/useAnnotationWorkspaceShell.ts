import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'

import { useEpisodeAnnotations, useSaveAnnotation } from '@/hooks/use-annotations'
import { useEpisodeEdits, useSaveEpisodeEdits } from '@/hooks/use-episode-edits'
import { KeyboardShortcutsContext } from '@/hooks/use-keyboard-shortcuts'
import { useSaveEpisodeLabels } from '@/hooks/use-labels'
import { usePrincipalContext } from '@/hooks/use-principal-context'
import { ApiClientError } from '@/lib/api-client'
import { isDiagnosticsEnabled, recordDiagnosticEvent } from '@/lib/playback-diagnostics'
import {
  useAnnotationStore,
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
  visible?: boolean
  diagnosticsVisible?: boolean
  canGoPreviousEpisode?: boolean
  onPreviousEpisode?: () => void
  canGoNextEpisode?: boolean
  onNextEpisode?: () => void
  onSaveAndNextEpisode?: () => void
}

export function useAnnotationWorkspaceShell({
  visible = true,
  diagnosticsVisible = isDiagnosticsEnabled(),
  canGoPreviousEpisode = false,
  onPreviousEpisode,
  canGoNextEpisode = false,
  onNextEpisode,
  onSaveAndNextEpisode,
}: UseAnnotationWorkspaceShellOptions) {
  const shortcutsEnabled = useContext(KeyboardShortcutsContext)
  const [exportDialogOpen, setExportDialogOpen] = useState(false)
  const [activeTab, setActiveTab] = useState('trajectory')
  const seekVideoFrameRef = useRef(
    (frame: number, _range: [number, number] | null, _constrainToRange = true) => frame,
  )
  const resumePlaybackRef = useRef((_: number) => {})

  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const annotationQuery = useEpisodeAnnotations()
  const currentAnnotation = useAnnotationStore((state) => state.currentAnnotation)
  const annotationDraftHydrated = useAnnotationStore((state) => state.draftHydrated)
  const annotationDraftError = useAnnotationStore((state) => state.draftError)
  const hasAnnotationChanges = useAnnotationStore((state) => state.isDirty)
  const annotationConflict = useAnnotationStore((state) => state.conflict)
  const annotationEditGeneration = useAnnotationStore((state) => state.editGeneration)
  const annotationContextGeneration = useAnnotationStore((state) => state.contextGeneration)
  const resetAnnotation = useAnnotationStore((state) => state.resetAnnotation)
  const saveAnnotation = useSaveAnnotation()
  const principalQuery = usePrincipalContext()
  const currentEpisode = useEpisodeStore((state) => state.currentEpisode)
  const labelDataLoaded = useLabelStore((state) => state.isLoaded)
  const labelDraftHydrated = useLabelStore((state) => state.draftHydrated)
  const labelDraftError = useLabelStore((state) => state.draftError)
  const labelsSaving = useLabelStore((state) => state.isSaving)
  const labelConflict = useLabelStore((state) => state.conflict)
  const availableLabels = useLabelStore((state) => state.availableLabels)
  const episodeLabels = useLabelStore((state) => state.episodeLabels)
  const savedEpisodeLabels = useLabelStore((state) => state.savedEpisodeLabels)
  const setEpisodeLabelsInStore = useLabelStore((state) => state.setEpisodeLabels)
  const removedFrames = useEditStore((state) => state.removedFrames)
  const clearTransforms = useEditStore((state) => state.clearTransforms)
  const savedEdits = useEpisodeEdits(
    currentDataset?.id ?? null,
    currentEpisode?.meta.index ?? null,
    principalQuery.data?.scopeId,
  )
  const saveEdits = useSaveEpisodeEdits()
  const subtasks = useEditStore((state) => state.subtasks)
  const addSubtask = useEditStore((state) => state.addSubtask)
  const globalTransform = useEditStore((state) => state.globalTransform)
  const cameraTransforms = useEditStore((state) => state.cameraTransforms)
  const { insertedFrames } = useFrameInsertionState()
  const { isDirty: hasEdits, resetEdits } = useEditDirtyState()
  const {
    currentFrame,
    isPlaying,
    playbackSpeed,
    setCurrentFrame,
    togglePlayback,
    setPlaybackSpeed,
  } = usePlaybackControls()
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
  useEffect(() => {
    if (!visible && isPlaying) togglePlayback()
  }, [visible, isPlaying, togglePlayback])
  const annotationAccessLost =
    annotationQuery.error instanceof ApiClientError &&
    [401, 403, 404].includes(annotationQuery.error.status)
  const saveBlockedReason =
    !principalQuery.data?.scopeId || principalQuery.error
      ? 'Reload your identity before saving.'
      : annotationAccessLost
        ? 'Annotation access is unavailable. Reload before saving.'
        : currentEpisode?.sourceRevision &&
            savedEdits.data &&
            (currentEpisode.sourceId !== savedEdits.data.sourceId ||
              currentEpisode.sourceRevision !== savedEdits.data.sourceRevision)
          ? 'The episode source changed. Reload and resolve the draft before saving.'
          : annotationConflict || labelConflict
            ? 'Resolve conflicting changes before saving.'
            : annotationDraftError ||
              labelDraftError ||
              savedEdits.persistenceError ||
              (labelsSaving ? 'Saving episode labels.' : null) ||
              (!annotationDraftHydrated ? 'Loading annotation drafts.' : null) ||
              (!labelDraftHydrated ? 'Loading label drafts.' : null) ||
              (!savedEdits.isReady ? 'Loading saved edits.' : null) ||
              (!labelDataLoaded ? 'Loading labels.' : null)

  const {
    hasPendingEpisodeChanges,
    saveStatusMessage,
    handleResetAll,
    handleSaveAndNextEpisode,
    handleSaveEpisode,
    isSaving,
  } = useAnnotationWorkspaceEpisodeActions({
    diagnosticsEnabled,
    currentDatasetId: currentDataset?.id ?? null,
    currentEpisodeIndex: currentEpisode?.meta.index ?? null,
    currentEpisodeLabels,
    savedLabelsForCurrentEpisode,
    availableLabels,
    labelDataLoaded,
    hasEdits,
    hasAnnotationChanges,
    principalScopeId: principalQuery.data?.scopeId,
    changeGeneration: JSON.stringify([
      annotationContextGeneration,
      annotationEditGeneration,
      currentEpisodeLabels,
    ]),
    saveBlockedReason,
    onSaveEpisodeAnnotation: async () => {
      if (
        !currentDataset ||
        !currentEpisode ||
        !currentAnnotation ||
        currentAnnotation.annotatorId !== principalQuery.data?.scopeId
      ) {
        throw new Error('Load the current annotation before saving.')
      }
      await saveAnnotation.mutateAsync({
        datasetId: currentDataset.id,
        episodeIndex: currentEpisode.meta.index,
        annotation: structuredClone(currentAnnotation),
      })
    },
    onResetEdits: () => {
      resetEdits()
      if (hasAnnotationChanges) resetAnnotation()
    },
    onSetEpisodeLabels: setEpisodeLabelsInStore,
    onSaveEpisodeDraft: async () => {
      const current = useEditStore.getState()
      const operations = current.getEditOperations()
      if (!savedEdits.isReady || !current.serverBaseline || !operations) {
        throw new Error('Load and resolve saved edits before saving.')
      }
      await saveEdits.mutateAsync({
        ...current.serverBaseline,
        operations: structuredClone(operations),
      })
    },
    onSaveEpisodeLabels: saveEpisodeLabels.mutateAsync,
    onRecordEvent: recordDiagnosticEvent,
    canGoNextEpisode,
    onAdvanceToNextEpisode: onSaveAndNextEpisode ?? onNextEpisode,
  })

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

  const handleTabChange = useCallback(
    (nextTab: string) => {
      setActiveTab(nextTab)
      recordDiagnosticEvent('workspace', 'tab-change', {
        previousTab: activeTab,
        nextTab,
      })
    },
    [activeTab],
  )

  const playback = useAnnotationWorkspacePlayback({
    autoLoop,
    currentFrame,
    currentDatasetId: currentDataset?.id ?? null,
    currentEpisodeIndex: currentEpisode?.meta.index ?? null,
    isPlaying,
    subtasks,
    totalFrames,
    onSeekFrame: (frame, range, constrainToRange) =>
      seekVideoFrameRef.current(frame, range, constrainToRange),
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
    isPlaying: visible && isPlaying,
    playbackSpeed,
    autoPlay: visible && autoPlay,
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

  const handleCreateSubtaskFromSelection = useCallback(
    (range: [number, number]) => {
      const nextSegment = createDefaultSubtask(range, subtasks)

      addSubtask(nextSegment)
      playback.handleCreateSubtaskFromRange(nextSegment)
    },
    [addSubtask, playback, subtasks],
  )

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!shortcutsEnabled || event.defaultPrevented) return
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        if (!event.repeat) void handleSaveEpisode()
        return
      }
      if (event.key !== 'Escape' || !playback.selectedRange) {
        return
      }

      playback.clearPlaybackSelection()
    }

    window.addEventListener('keydown', handleKeyDown)

    return () => {
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [playback, handleSaveEpisode, shortcutsEnabled])

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
    canSaveEpisode:
      !saveBlockedReason &&
      !saveEpisodeLabels.isPending &&
      !saveAnnotation.isPending &&
      !saveEdits.isPending,
    clearTransforms,
    currentDataset,
    currentEpisode,
    currentFrame,
    diagnostics,
    diagnosticsEnabled,
    displayFilter: media.displayFilter,
    exportDialogOpen,
    editPersistenceError: savedEdits.persistenceError,
    resolveRecoveredEdits: savedEdits.resolveRecoveredEdits,
    frameImageUrl: media.frameImageUrl,
    frameImageUrls: media.frameImageUrls,
    globalTransform,
    cameraTransforms,
    handleCreateSubtaskFromSelection,
    handleLoadedMetadata: media.handleLoadedMetadata,
    handleOpenExportDialog,
    handleResetAllClick,
    handleSaveAndNextEpisode,
    handleSaveEpisode,
    isSaving,
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
    videoUrls: media.videoUrls,
    canvasRef: media.canvasRef,
    cameras: media.cameras,
    selectedCameras: media.selectedCameras,
    setSelectedCameras: media.setSelectedCameras,
    originalFrameIndex,
    cameraName: media.cameraName,
    setCameraName: media.setCameraName,
  }
}
