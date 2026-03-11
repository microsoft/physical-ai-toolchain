import { type DataviewerDiagnosticEvent,DIAGNOSTIC_CHANNEL_OPTIONS } from '@/lib/playback-diagnostics'

export interface WorkspaceDiagnosticsSummaryInput {
  activeTab: string
  currentDatasetId: string | null
  currentEpisodeIndex: number | null
  currentFrame: number
  totalFrames: number
  diagnosticsChannels: readonly string[]
  isPlaying: boolean
  selectedRange: [number, number] | null
  selectedSubtaskId: string | null
}

export interface WorkspaceDiagnosticsSummaryEntry {
  label: string
  value: string
}

export interface RecentDiagnosticEvent extends DataviewerDiagnosticEvent {
  uniqueKey: string
}

export function buildDiagnosticsStateSummary({
  activeTab,
  currentDatasetId,
  currentEpisodeIndex,
  currentFrame,
  totalFrames,
  diagnosticsChannels,
  isPlaying,
  selectedRange,
  selectedSubtaskId,
}: WorkspaceDiagnosticsSummaryInput): WorkspaceDiagnosticsSummaryEntry[] {
  return [
    { label: 'Dataset', value: currentDatasetId ?? 'none' },
    { label: 'Episode', value: currentEpisodeIndex === null ? 'none' : String(currentEpisodeIndex) },
    { label: 'Tab', value: activeTab },
    { label: 'Frame', value: `${currentFrame} / ${Math.max(totalFrames - 1, 0)}` },
    { label: 'Playback', value: isPlaying ? 'playing' : 'paused' },
    { label: 'Selection', value: getSelectionSummary(selectedSubtaskId, selectedRange) },
    {
      label: 'Channels',
      value: diagnosticsChannels.length > 0 ? diagnosticsChannels.join(', ') : 'none',
    },
  ]
}

export function getAvailableDiagnosticsChannels(
  diagnosticsChannels: readonly string[],
  diagnosticEvents: readonly DataviewerDiagnosticEvent[],
): string[] {
  const configuredChannels = new Set(diagnosticsChannels.filter((channel) => channel !== 'all'))
  const eventChannels = new Set(diagnosticEvents.map((event) => event.channel))
  const channels = DIAGNOSTIC_CHANNEL_OPTIONS.filter((channel) => {
    if (channel === 'all') {
      return true
    }

    return configuredChannels.has(channel) || eventChannels.has(channel)
  })

  return channels.length > 0 ? [...channels] : ['all']
}

export function getVisibleDiagnosticEvents(
  diagnosticEvents: readonly DataviewerDiagnosticEvent[],
  selectedDiagnosticsChannel: string,
) {
  if (selectedDiagnosticsChannel === 'all') {
    return [...diagnosticEvents]
  }

  return diagnosticEvents.filter((event) => event.channel === selectedDiagnosticsChannel)
}

export function getRecentDiagnosticEvents(
  visibleDiagnosticEvents: readonly DataviewerDiagnosticEvent[],
): RecentDiagnosticEvent[] {
  const keyCounts = new Map<string, number>()

  return visibleDiagnosticEvents.slice(-12).map((event) => {
    const baseKey = `${event.timestamp}-${event.channel}-${event.type}-${JSON.stringify(event.data ?? {})}`
    const nextCount = (keyCounts.get(baseKey) ?? 0) + 1

    keyCounts.set(baseKey, nextCount)

    return {
      ...event,
      uniqueKey: `${baseKey}-${nextCount}`,
    }
  })
}

function getSelectionSummary(selectedSubtaskId: string | null, selectedRange: [number, number] | null) {
  if (selectedSubtaskId) {
    return `subtask:${selectedSubtaskId}`
  }

  if (selectedRange) {
    return `${selectedRange[0]}-${selectedRange[1]}`
  }

  return 'none'
}
