import { type ReactNode, useEffect, useState } from 'react'

import { AnnotationWorkspace } from '@/components/annotation-workspace/AnnotationWorkspace'
import { useEpisode } from '@/hooks/use-datasets'
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
  const { data: episode, isLoading, error } = useEpisode(datasetId, episodeIndex)
  const setCurrentEpisode = useEpisodeStore((state) => state.setCurrentEpisode)
  const [navigationStatus, setNavigationStatus] = useState<string | null>(null)

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
  } else if (error) {
    content = (
      <div className="flex h-full items-center justify-center">
        <div role="alert" className="text-status-danger-foreground">
          Error loading episode: {error.message}
        </div>
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
    <>
      {episode && !isLoading && !error && (
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
      {content}
    </>
  )
}
