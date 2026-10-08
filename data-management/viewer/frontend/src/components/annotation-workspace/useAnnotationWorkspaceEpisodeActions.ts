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
  hasAnnotationChanges?: boolean
  onSaveEpisodeAnnotation?: () => void | Promise<unknown>
  principalScopeId?: string
  changeGeneration?: string
  saveBlockedReason?: string | null
  onResetEdits: () => void
  onSetEpisodeLabels: (episodeIndex: number, labels: string[]) => void
  onSaveEpisodeDraft: () => void | Promise<unknown>
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
  hasAnnotationChanges = false,
  onSaveEpisodeAnnotation,
  principalScopeId,
  changeGeneration,
  saveBlockedReason,
  onResetEdits,
  onSetEpisodeLabels,
  onSaveEpisodeDraft,
  onSaveEpisodeLabels,
  onRecordEvent,
  canGoNextEpisode,
  onAdvanceToNextEpisode,
}: UseAnnotationWorkspaceEpisodeActionsOptions) {
  const [showSavedStatus, setShowSavedStatus] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const savingRef = useRef(false)
  const contextKey = JSON.stringify([currentDatasetId, currentEpisodeIndex, principalScopeId])
  const currentSaveContext = useRef({ contextKey, changeGeneration })
  useEffect(() => {
    currentSaveContext.current = { contextKey, changeGeneration }
  }, [contextKey, changeGeneration])
  useEffect(() => {
    setSaveError(null)
    setShowSavedStatus(false)
  }, [contextKey])
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

  const hasPendingEpisodeChanges = hasLabelChanges || hasEdits || hasAnnotationChanges
  const saveStatusMessage =
    saveError ??
    saveBlockedReason ??
    (isSaving
      ? 'Saving episode changes.'
      : hasPendingEpisodeChanges
        ? 'Unsaved episode changes.'
        : showSavedStatus
          ? 'Episode changes saved.'
          : null)

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
    if (
      hasPendingEpisodeChanges &&
      (globalThis.confirm?.(
        'Discard unsaved annotation, label, and frame-edit changes for this episode?',
      ) ?? true) === false
    ) {
      return
    }

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
    hasPendingEpisodeChanges,
    onResetEdits,
    onSetEpisodeLabels,
    savedLabelsForCurrentEpisode,
  ])

  const saveEpisode = useCallback(
    async (advance: boolean) => {
      if (currentEpisodeIndex === null || !currentDatasetId || savingRef.current) {
        return
      }
      if (saveBlockedReason) {
        setSaveError(saveBlockedReason)
        return
      }
      savingRef.current = true
      setIsSaving(true)
      setSaveError(null)
      const submittedContext = { contextKey, changeGeneration }
      try {
        const writes: Promise<void>[] = []
        const write = async (resource: string, operation: () => void | Promise<unknown>) => {
          await operation()
          onRecordEvent(
            resource === 'labels' ? 'labels' : 'persistence',
            resource === 'labels' ? 'saved' : `${resource}-saved`,
            {
              datasetId: currentDatasetId,
              episodeIndex: currentEpisodeIndex,
            },
          )
        }
        if (hasLabelChanges)
          writes.push(
            write('labels', () =>
              onSaveEpisodeLabels({
                episodeIdx: currentEpisodeIndex,
                labels: [...currentEpisodeLabels],
              }),
            ),
          )
        if (hasEdits) writes.push(write('edits', onSaveEpisodeDraft))
        if (hasAnnotationChanges)
          writes.push(
            write('annotation', () => {
              if (!onSaveEpisodeAnnotation) throw new Error('Annotation persistence is unavailable')
              return onSaveEpisodeAnnotation()
            }),
          )
        const results = await Promise.allSettled(writes)
        const failures = results.filter((result) => result.status === 'rejected').length
        const sameContext = currentSaveContext.current.contextKey === submittedContext.contextKey
        if (failures) {
          if (sameContext)
            setSaveError(
              failures === results.length
                ? 'Episode changes could not be saved. Your draft is retained.'
                : 'Some episode changes could not be saved. Unsaved drafts are retained.',
            )
          onRecordEvent('persistence', 'episode-save-failed', {
            datasetId: currentDatasetId,
            episodeIndex: currentEpisodeIndex,
            failedResources: failures,
            savedResources: results.length - failures,
          })
          return
        }
        if (
          !sameContext ||
          currentSaveContext.current.changeGeneration !== submittedContext.changeGeneration
        )
          return
        if (hasPendingEpisodeChanges) announceSave()
        const shouldAdvance = advance && canGoNextEpisode && Boolean(onAdvanceToNextEpisode)
        onRecordEvent('workspace', shouldAdvance ? 'save-next-episode' : 'save-episode', {
          datasetId: currentDatasetId,
          episodeIndex: currentEpisodeIndex,
          hasPendingEpisodeChanges,
          hasEdits,
          hasLabelChanges,
          hasAnnotationChanges,
        })
        if (shouldAdvance) onAdvanceToNextEpisode?.()
      } finally {
        savingRef.current = false
        setIsSaving(false)
      }
    },
    [
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
      hasAnnotationChanges,
      onSaveEpisodeAnnotation,
      contextKey,
      changeGeneration,
      saveBlockedReason,
    ],
  )
  const handleSaveEpisode = useCallback(() => saveEpisode(false), [saveEpisode])
  const handleSaveAndNextEpisode = useCallback(() => saveEpisode(true), [saveEpisode])

  return {
    hasLabelChanges,
    hasPendingEpisodeChanges,
    saveStatusMessage,
    handleResetAll,
    handleSaveAndNextEpisode,
    handleSaveEpisode,
    isSaving,
  }
}
