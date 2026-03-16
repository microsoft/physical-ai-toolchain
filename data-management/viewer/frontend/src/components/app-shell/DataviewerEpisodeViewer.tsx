import { useEffect } from 'react'

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

  useEffect(() => {
    if (episode) {
      setCurrentEpisode(episode)
    }
  }, [episode, setCurrentEpisode])

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-muted-foreground">Loading episode {episodeIndex}...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-red-500">Error loading episode: {error.message}</div>
      </div>
    )
  }

  if (!episode) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-muted-foreground">No episode data</div>
      </div>
    )
  }

  return (
    <AnnotationWorkspace
      diagnosticsVisible={diagnosticsVisible}
      canGoPreviousEpisode={canGoPreviousEpisode}
      onPreviousEpisode={onPreviousEpisode}
      canGoNextEpisode={canGoNextEpisode}
      onNextEpisode={onNextEpisode}
      onSaveAndNextEpisode={onSaveAndNextEpisode}
    />
  )
}
