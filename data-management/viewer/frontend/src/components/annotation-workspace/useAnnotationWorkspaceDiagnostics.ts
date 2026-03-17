import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  buildDiagnosticsStateSummary,
  getAvailableDiagnosticsChannels,
  getRecentDiagnosticEvents,
  getVisibleDiagnosticEvents,
} from '@/components/annotation-workspace/annotation-workspace-diagnostics'
import {
  clearDiagnosticEvents,
  DIAGNOSTICS_EVENT_NAME,
  getEnabledDiagnosticsChannels,
  isDiagnosticsEnabled,
  readDiagnosticEvents,
  recordDiagnosticEvent,
  stringifyDiagnosticEvents,
} from '@/lib/playback-diagnostics'

interface UseAnnotationWorkspaceDiagnosticsOptions {
  diagnosticsVisible: boolean
  activeTab: string
  currentDatasetId: string | null
  currentEpisodeIndex: number | null
  currentFrame: number
  totalFrames: number
  isPlaying: boolean
  selectedRange: [number, number] | null
  selectedSubtaskId: string | null
}

export function useAnnotationWorkspaceDiagnostics({
  diagnosticsVisible,
  activeTab,
  currentDatasetId,
  currentEpisodeIndex,
  currentFrame,
  totalFrames,
  isPlaying,
  selectedRange,
  selectedSubtaskId,
}: UseAnnotationWorkspaceDiagnosticsOptions) {
  const [diagnosticEvents, setDiagnosticEvents] = useState(() =>
    diagnosticsVisible && isDiagnosticsEnabled() ? readDiagnosticEvents() : [],
  )
  const [selectedDiagnosticsChannel, setSelectedDiagnosticsChannel] = useState('all')
  const [diagnosticsClipboardStatus, setDiagnosticsClipboardStatus] = useState<string | null>(null)
  const wasDiagnosticsEnabledRef = useRef(diagnosticsVisible && isDiagnosticsEnabled())

  const diagnosticsEnabled = diagnosticsVisible && isDiagnosticsEnabled()
  const diagnosticsChannels = getEnabledDiagnosticsChannels()

  useEffect(() => {
    if (!isDiagnosticsEnabled() || typeof window === 'undefined') {
      return
    }

    const syncDiagnostics = () => {
      setDiagnosticEvents(readDiagnosticEvents())
    }

    syncDiagnostics()
    window.addEventListener(DIAGNOSTICS_EVENT_NAME, syncDiagnostics)

    return () => {
      window.removeEventListener(DIAGNOSTICS_EVENT_NAME, syncDiagnostics)
    }
  }, [diagnosticsVisible])

  useEffect(() => {
    if (diagnosticsEnabled && !wasDiagnosticsEnabledRef.current) {
      recordDiagnosticEvent('workspace', 'diagnostics-enabled', {
        activeTab,
        episodeIndex: currentEpisodeIndex,
      })
      setDiagnosticEvents(readDiagnosticEvents())
    }

    if (!diagnosticsEnabled && wasDiagnosticsEnabledRef.current) {
      setDiagnosticEvents([])
    }

    wasDiagnosticsEnabledRef.current = diagnosticsEnabled
  }, [activeTab, currentEpisodeIndex, diagnosticsEnabled])

  useEffect(() => {
    if (diagnosticsEnabled) {
      return
    }

    setSelectedDiagnosticsChannel('all')
    setDiagnosticsClipboardStatus(null)
  }, [diagnosticsEnabled])

  const diagnosticsStateSummary = useMemo(
    () =>
      buildDiagnosticsStateSummary({
        activeTab,
        currentDatasetId,
        currentEpisodeIndex,
        currentFrame,
        totalFrames,
        diagnosticsChannels,
        isPlaying,
        selectedRange,
        selectedSubtaskId,
      }),
    [
      activeTab,
      currentDatasetId,
      currentEpisodeIndex,
      currentFrame,
      diagnosticsChannels,
      isPlaying,
      selectedRange,
      selectedSubtaskId,
      totalFrames,
    ],
  )

  const availableDiagnosticsChannels = useMemo(
    () => getAvailableDiagnosticsChannels(diagnosticsChannels, diagnosticEvents),
    [diagnosticEvents, diagnosticsChannels],
  )

  const visibleDiagnosticEvents = useMemo(
    () => getVisibleDiagnosticEvents(diagnosticEvents, selectedDiagnosticsChannel),
    [diagnosticEvents, selectedDiagnosticsChannel],
  )

  const recentDiagnosticEvents = useMemo(
    () => getRecentDiagnosticEvents(visibleDiagnosticEvents),
    [visibleDiagnosticEvents],
  )

  const serializedDiagnosticEvents = useMemo(
    () => stringifyDiagnosticEvents(visibleDiagnosticEvents),
    [visibleDiagnosticEvents],
  )

  const handleClearVisibleDiagnostics = useCallback(() => {
    const channel = selectedDiagnosticsChannel === 'all' ? undefined : selectedDiagnosticsChannel
    clearDiagnosticEvents(channel)
    setDiagnosticEvents(readDiagnosticEvents(channel))
    setDiagnosticsClipboardStatus('Cleared visible diagnostics events.')
  }, [selectedDiagnosticsChannel])

  const handleCopyDiagnostics = useCallback(async () => {
    if (!navigator.clipboard) {
      setDiagnosticsClipboardStatus('Clipboard is unavailable in this browser.')
      return
    }

    await navigator.clipboard.writeText(serializedDiagnosticEvents)
    setDiagnosticsClipboardStatus('Copied diagnostics JSON.')
  }, [serializedDiagnosticEvents])

  const handleDownloadDiagnostics = useCallback(() => {
    if (typeof document === 'undefined') {
      return
    }

    const blob = new Blob([serializedDiagnosticEvents], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')

    link.href = url
    link.download =
      selectedDiagnosticsChannel === 'all'
        ? 'dataviewer-diagnostics.json'
        : `dataviewer-diagnostics-${selectedDiagnosticsChannel}.json`
    link.click()
    URL.revokeObjectURL(url)
    setDiagnosticsClipboardStatus('Downloaded diagnostics JSON.')
  }, [selectedDiagnosticsChannel, serializedDiagnosticEvents])

  return {
    diagnosticsEnabled,
    diagnosticsStateSummary,
    availableDiagnosticsChannels,
    selectedDiagnosticsChannel,
    setSelectedDiagnosticsChannel,
    diagnosticsClipboardStatus,
    recentDiagnosticEvents,
    handleClearVisibleDiagnostics,
    handleCopyDiagnostics,
    handleDownloadDiagnostics,
  }
}
