import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useCacheStats } from '@/hooks/use-datasets'

import type {
  RecentDiagnosticEvent,
  WorkspaceDiagnosticsSummaryEntry,
} from './annotation-workspace-diagnostics'

interface AnnotationWorkspaceDiagnosticsPanelProps {
  diagnosticsStateSummary: readonly WorkspaceDiagnosticsSummaryEntry[]
  availableDiagnosticsChannels: readonly string[]
  selectedDiagnosticsChannel: string
  onSelectedDiagnosticsChannelChange: (channel: string) => void
  onClearVisibleDiagnostics: () => void
  onCopyDiagnostics: () => void
  onDownloadDiagnostics: () => void
  diagnosticsClipboardStatus: string | null
  recentDiagnosticEvents: readonly RecentDiagnosticEvent[]
  playbackRangeStart: number
  playbackRangeEnd: number
  shouldLoopPlaybackRange: boolean
}

export function AnnotationWorkspaceDiagnosticsPanel({
  diagnosticsStateSummary,
  availableDiagnosticsChannels,
  selectedDiagnosticsChannel,
  onSelectedDiagnosticsChannelChange,
  onClearVisibleDiagnostics,
  onCopyDiagnostics,
  onDownloadDiagnostics,
  diagnosticsClipboardStatus,
  recentDiagnosticEvents,
  playbackRangeStart,
  playbackRangeEnd,
  shouldLoopPlaybackRange,
}: AnnotationWorkspaceDiagnosticsPanelProps) {
  const { data: cacheStats } = useCacheStats(true)

  return (
    <Card className="shrink-0" data-testid="dataviewer-diagnostics-panel">
      <CardHeader className="px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-sm">Dataviewer Diagnostics</CardTitle>
            <p className="text-muted-foreground text-xs">
              Whole-workspace diagnostics are enabled. Use the header toggle to hide this panel.
            </p>
          </div>
          <div className="text-muted-foreground text-right text-xs">
            <div>
              Range: {playbackRangeStart} to {playbackRangeEnd}
            </div>
            <div>Loop intent: {shouldLoopPlaybackRange ? 'enabled' : 'disabled'}</div>
          </div>
        </div>
      </CardHeader>
      <CardContent className="grid gap-3 border-t p-4 lg:grid-cols-[minmax(260px,320px)_minmax(0,1fr)]">
        <div className="bg-muted/20 rounded-lg border p-3">
          <h4 className="text-sm font-medium">Workspace State</h4>
          <div className="mt-2 grid gap-1 text-xs">
            {diagnosticsStateSummary.map((entry) => (
              <div
                key={entry.label}
                className="border-border/50 flex items-center justify-between gap-3 border-b py-1 last:border-b-0"
              >
                <span className="text-muted-foreground">{entry.label}</span>
                <span>{entry.value}</span>
              </div>
            ))}
          </div>
          {cacheStats && (
            <>
              <h4 className="mt-3 text-sm font-medium">Episode Cache</h4>
              <div className="mt-2 grid gap-1 text-xs">
                {[
                  { label: 'Capacity', value: String(cacheStats.capacity) },
                  { label: 'Cached Episodes', value: String(cacheStats.size) },
                  {
                    label: 'Memory',
                    value: `${(cacheStats.totalBytes / 1024).toFixed(0)} KB / ${cacheStats.maxMemoryBytes > 0 ? `${(cacheStats.maxMemoryBytes / (1024 * 1024)).toFixed(0)} MB` : '∞'}`,
                  },
                  { label: 'Hits / Misses', value: `${cacheStats.hits} / ${cacheStats.misses}` },
                  { label: 'Hit Rate', value: `${(cacheStats.hitRate * 100).toFixed(1)}%` },
                ].map((entry) => (
                  <div
                    key={entry.label}
                    className="border-border/50 flex items-center justify-between gap-3 border-b py-1 last:border-b-0"
                  >
                    <span className="text-muted-foreground">{entry.label}</span>
                    <span>{entry.value}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
        <div className="bg-muted/20 rounded-lg border p-3">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h4 className="text-sm font-medium">Recent Events</h4>
              <p className="text-muted-foreground text-xs">
                Filter, clear, copy, or download the visible diagnostics stream.
              </p>
            </div>
            <div className="flex flex-col items-stretch gap-2 sm:min-w-[240px] sm:items-end">
              <label className="text-muted-foreground flex items-center gap-2 text-xs">
                <span>Filter Events</span>
                <select
                  aria-label="Filter Events"
                  className="bg-background text-foreground h-8 rounded-md border px-2 text-xs"
                  value={selectedDiagnosticsChannel}
                  onChange={(event) => onSelectedDiagnosticsChannelChange(event.target.value)}
                >
                  {availableDiagnosticsChannels.map((channel) => (
                    <option key={channel} value={channel}>
                      {channel}
                    </option>
                  ))}
                </select>
              </label>
              <div className="flex flex-wrap gap-2 sm:justify-end">
                <Button size="sm" variant="outline" onClick={onClearVisibleDiagnostics}>
                  Clear Visible Events
                </Button>
                <Button size="sm" variant="outline" onClick={onCopyDiagnostics}>
                  Copy JSON
                </Button>
                <Button size="sm" variant="outline" onClick={onDownloadDiagnostics}>
                  Download JSON
                </Button>
              </div>
              {diagnosticsClipboardStatus && (
                <p className="text-muted-foreground text-right text-xs">
                  {diagnosticsClipboardStatus}
                </p>
              )}
            </div>
          </div>
          <div className="bg-background/80 mt-2 max-h-48 overflow-y-auto rounded-sm border p-2 font-mono text-[11px]">
            {recentDiagnosticEvents.length === 0 ? (
              <div className="text-muted-foreground">No diagnostics events recorded yet.</div>
            ) : (
              recentDiagnosticEvents.map((event) => (
                <div
                  key={event.uniqueKey}
                  className="border-border/50 border-b py-1 last:border-b-0"
                >
                  <div>{event.channel}</div>
                  <div>{event.type}</div>
                  <div className="text-muted-foreground">{JSON.stringify(event.data ?? {})}</div>
                </div>
              ))
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
