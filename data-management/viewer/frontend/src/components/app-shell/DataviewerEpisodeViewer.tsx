import { RefreshCw } from 'lucide-react'
import { type ReactNode, useEffect, useState } from 'react'

import { AnnotationWorkspace } from '@/components/annotation-workspace/AnnotationWorkspace'
import { Button } from '@/components/ui/button'
import { useEpisode } from '@/hooks/use-datasets'
import { recordDiagnosticEvent } from '@/lib/playback-diagnostics'
import { useEpisodeStore } from '@/stores'

interface DataviewerEpisodeViewerProps {
  datasetId: string
  episodeIndex: number
  diagnosticsVisible: boolean
  canGoPreviousEpisode: boolean
  onPreviousEpisode: () => void
  canGoNextEpisode: boolean
  onNextEpisode: () => void
  onSaveAndNextEpisode: () => void
}

export function DataviewerEpisodeViewer({
  datasetId,
  episodeIndex,
  diagnosticsVisible,
  canGoPreviousEpisode,
  onPreviousEpisode,
  canGoNextEpisode,
  onNextEpisode,
  onSaveAndNextEpisode,
}: DataviewerEpisodeViewerProps) {
  const {
    data: episode,
    isLoading,
    isFetching,
    error,
    refetch,
  } = useEpisode(datasetId, episodeIndex)
  const setCurrentEpisode = useEpisodeStore((state) => state.setCurrentEpisode)
  const [navigationStatus, setNavigationStatus] = useState<string | null>(null)
  const hasCachedEpisode = !!episode

  useEffect(() => {
    if (error) {
      recordDiagnosticEvent('workspace', 'episode-fetch-error', {
        datasetId,
        episodeIndex,
        hasCachedEpisode,
        message: error.message,
      })
    }
  }, [datasetId, episodeIndex, error, hasCachedEpisode])

  const retryEpisode = () => {
    recordDiagnosticEvent('workspace', 'episode-fetch-retry', {
      datasetId,
      episodeIndex,
      hasCachedEpisode,
    })
    void refetch()
  }

  useEffect(() => {
    if (episode) {
      setCurrentEpisode(episode)
    }
  }, [episode, setCurrentEpisode])

  useEffect(() => {
    if (!navigationStatus) return
    const timeout = window.setTimeout(() => setNavigationStatus(null), 2400)
    return () => window.clearTimeout(timeout)
  }, [navigationStatus])

  let content: ReactNode
  if (isLoading) {
    content = (
      <div className="flex h-full items-center justify-center">
        <div role="status" aria-live="polite" className="text-muted-foreground">
          Loading episode {episodeIndex}...
        </div>
      </div>
    )
  } else if (error && !episode) {
    content = (
      <div className="flex h-full flex-col items-center justify-center gap-2">
        <div role="alert" className="text-status-danger-foreground">
          Error loading episode: {error.message}
        </div>
        <Button
          variant="outline"
          disabled={isFetching}
          onClick={retryEpisode}
          aria-label="Retry episode load"
        >
          <RefreshCw className="mr-2 size-4" aria-hidden="true" />
          Retry
        </Button>
      </div>
    )
  } else if (!episode) {
    content = (
      <div className="flex h-full items-center justify-center">
        <div role="status" className="text-muted-foreground">
          No episode data
        </div>
      </div>
    )
  } else {
    content = (
      <AnnotationWorkspace
        diagnosticsVisible={diagnosticsVisible}
        canGoPreviousEpisode={canGoPreviousEpisode}
        onPreviousEpisode={onPreviousEpisode}
        canGoNextEpisode={canGoNextEpisode}
        onNextEpisode={onNextEpisode}
        onSaveAndNextEpisode={() => {
          setNavigationStatus('Episode changes saved.')
          onSaveAndNextEpisode()
        }}
      />
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {episode && error && (
        <div className="bg-background flex shrink-0 items-center gap-2 border-b px-3 py-2">
          <div role="alert" className="text-status-danger-foreground min-w-0 flex-1 text-sm">
            Could not refresh episode. Showing previously loaded data.
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={isFetching}
            onClick={retryEpisode}
            aria-label="Retry episode load"
          >
            <RefreshCw className="mr-2 size-4" aria-hidden="true" />
            Retry
          </Button>
        </div>
      )}
      {episode && !isLoading && (
        <div
          role="status"
          aria-live="polite"
          aria-atomic="true"
          className="sr-only"
          data-testid="episode-navigation-status"
        >
          {navigationStatus}
        </div>
      )}
      <div className="min-h-0 flex-1">{content}</div>
    </div>
  )
}
