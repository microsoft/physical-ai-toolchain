import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

interface SaveEpisodeLabelsInput {
  episodeIdx: number
  labels: string[]
}

type SaveEpisodeLabelsResult = void | Promise<unknown>

interface UseAnnotationWorkspaceEpisodeActionsOptions {
  diagnosticsEnabled: boolean
  currentDatasetId: string | null
  currentEpisodeIndex: number | null
  currentEpisodeLabels: string[]
  savedLabelsForCurrentEpisode: string[]
  availableLabels: string[]
  labelDataLoaded: boolean
  hasEdits: boolean
  onResetEdits: () => void
  onSetEpisodeLabels: (episodeIndex: number, labels: string[]) => void
  onSaveEpisodeDraft: () => void
  onSaveEpisodeLabels: (input: SaveEpisodeLabelsInput) => SaveEpisodeLabelsResult
  onRecordEvent: (channel: string, type: string, data?: Record<string, unknown>) => void
  canGoNextEpisode: boolean
  onAdvanceToNextEpisode?: () => void
}

export function useAnnotationWorkspaceEpisodeActions({
  diagnosticsEnabled,
  currentDatasetId,
  currentEpisodeIndex,
  currentEpisodeLabels,
  savedLabelsForCurrentEpisode,
  availableLabels,
  labelDataLoaded,
  hasEdits,
  onResetEdits,
  onSetEpisodeLabels,
  onSaveEpisodeDraft,
  onSaveEpisodeLabels,
  onRecordEvent,
  canGoNextEpisode,
  onAdvanceToNextEpisode,
}: UseAnnotationWorkspaceEpisodeActionsOptions) {
  const [showSavedStatus, setShowSavedStatus] = useState(false)
  const saveStatusTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastLabelSignatureRef = useRef<string | null>(null)
  const lastEpisodeContextRef = useRef<string | null>(null)

  const labelSignature = useMemo(
    () => JSON.stringify([...currentEpisodeLabels].sort()),
    [currentEpisodeLabels],
  )

  const hasLabelChanges = useMemo(() => {
    if (currentEpisodeIndex === null || !labelDataLoaded) {
      return false
    }

    const current = [...currentEpisodeLabels].sort()
    const initial = [...savedLabelsForCurrentEpisode].sort()

    if (current.length !== initial.length) {
      return true
    }

    return current.some((label, index) => label !== initial[index])
  }, [currentEpisodeIndex, currentEpisodeLabels, labelDataLoaded, savedLabelsForCurrentEpisode])

  const hasPendingEpisodeChanges = hasLabelChanges || hasEdits
  const saveStatusMessage = hasPendingEpisodeChanges
    ? 'Unsaved episode changes.'
    : showSavedStatus
      ? 'Episode changes saved.'
      : null

  const announceSave = useCallback(() => {
    setShowSavedStatus(true)

    if (saveStatusTimeoutRef.current) {
      clearTimeout(saveStatusTimeoutRef.current)
    }

    saveStatusTimeoutRef.current = setTimeout(() => {
      setShowSavedStatus(false)
      saveStatusTimeoutRef.current = null
    }, 2400)
  }, [])

  useEffect(() => {
    return () => {
      if (saveStatusTimeoutRef.current) {
        clearTimeout(saveStatusTimeoutRef.current)
      }
    }
  }, [])

  useEffect(() => {
    if (currentEpisodeIndex === null) {
      lastLabelSignatureRef.current = null
      return
    }

    if (
      diagnosticsEnabled &&
      lastLabelSignatureRef.current !== null &&
      lastLabelSignatureRef.current !== labelSignature
    ) {
      onRecordEvent('labels', 'draft-change', {
        episodeIndex: currentEpisodeIndex,
        labelCount: currentEpisodeLabels.length,
        labels: [...currentEpisodeLabels],
        hasLabelChanges,
      })
    }

    lastLabelSignatureRef.current = labelSignature
  }, [
    currentEpisodeIndex,
    currentEpisodeLabels,
    diagnosticsEnabled,
    hasLabelChanges,
    labelSignature,
    onRecordEvent,
  ])

  useEffect(() => {
    const nextContext =
      currentDatasetId !== null && currentEpisodeIndex !== null
        ? `${currentDatasetId}:${currentEpisodeIndex}`
        : null

    if (!nextContext) {
      lastEpisodeContextRef.current = null
      return
    }

    if (
      diagnosticsEnabled &&
      lastEpisodeContextRef.current !== null &&
      lastEpisodeContextRef.current !== nextContext
    ) {
      const [previousDatasetId, previousEpisodeIndex] = lastEpisodeContextRef.current.split(':')

      onRecordEvent('navigation', 'episode-context-change', {
        previousDatasetId,
        previousEpisodeIndex: Number(previousEpisodeIndex),
        datasetId: currentDatasetId,
        episodeIndex: currentEpisodeIndex,
      })
    }

    lastEpisodeContextRef.current = nextContext
  }, [currentDatasetId, currentEpisodeIndex, diagnosticsEnabled, onRecordEvent])

  const handleResetAll = useCallback(async () => {
    onResetEdits()

    if (currentEpisodeIndex === null || !hasLabelChanges) {
      return
    }

    const nextLabels = savedLabelsForCurrentEpisode.filter((label) =>
      availableLabels.includes(label),
    )

    onSetEpisodeLabels(currentEpisodeIndex, nextLabels)
  }, [
    availableLabels,
    currentEpisodeIndex,
    hasLabelChanges,
    onResetEdits,
    onSetEpisodeLabels,
    savedLabelsForCurrentEpisode,
  ])

  const handleSaveAndNextEpisode = useCallback(async () => {
    if (!canGoNextEpisode || !onAdvanceToNextEpisode || currentEpisodeIndex === null) {
      return
    }

    if (currentDatasetId && hasLabelChanges) {
      await onSaveEpisodeLabels({
        episodeIdx: currentEpisodeIndex,
        labels: currentEpisodeLabels,
      })

      onRecordEvent('labels', 'saved', {
        datasetId: currentDatasetId,
        episodeIndex: currentEpisodeIndex,
        labelCount: currentEpisodeLabels.length,
      })
    }

    if (hasEdits) {
      onSaveEpisodeDraft()
      onRecordEvent('persistence', 'draft-saved', {
        datasetId: currentDatasetId,
        episodeIndex: currentEpisodeIndex,
      })
    }

    if (hasPendingEpisodeChanges) {
      announceSave()
    }

    onRecordEvent('workspace', 'save-next-episode', {
      episodeIndex: currentEpisodeIndex,
      hasPendingEpisodeChanges,
      hasEdits,
      hasLabelChanges,
    })

    onAdvanceToNextEpisode()
  }, [
    announceSave,
    canGoNextEpisode,
    currentDatasetId,
    currentEpisodeIndex,
    currentEpisodeLabels,
    hasEdits,
    hasLabelChanges,
    hasPendingEpisodeChanges,
    onAdvanceToNextEpisode,
    onRecordEvent,
    onSaveEpisodeDraft,
    onSaveEpisodeLabels,
  ])

  return {
    hasLabelChanges,
    hasPendingEpisodeChanges,
    saveStatusMessage,
    handleResetAll,
    handleSaveAndNextEpisode,
  }
}
