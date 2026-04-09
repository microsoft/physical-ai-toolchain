import type { DatasetInfo, EpisodeData } from '@/types'
import type { ColorAdjustment, FrameInsertion, ImageTransform } from '@/types/episode-edit'

import { useAnnotationWorkspaceMediaSources } from './useAnnotationWorkspaceMediaSources'
import { useAnnotationWorkspaceVideoSync } from './useAnnotationWorkspaceVideoSync'
import { useFramePrefetch } from './useFramePrefetch'

interface UseAnnotationWorkspaceMediaControllerOptions {
  currentDataset: DatasetInfo | null
  currentEpisode: EpisodeData | null
  currentFrame: number
  totalFrames: number
  originalFrameIndex: number | null
  activePlaybackRange: [number, number] | null
  playbackRangeStart: number
  playbackRangeEnd: number
  isPlaying: boolean
  playbackSpeed: number
  autoPlay: boolean
  autoLoop: boolean
  shouldLoopPlaybackRange: boolean
  displayAdjustment: ColorAdjustment | null
  displayActive: boolean
  globalTransform: ImageTransform | null
  insertedFrames: Map<number, FrameInsertion>
  removedFrames: Set<number>
  onSetCurrentFrame: (frame: number) => void
  onTogglePlayback: () => void
  onSetFrameWithinPlaybackRange: (frame: number) => void
  onRecordEvent: (channel: string, type: string, data?: Record<string, unknown>) => void
}

export function useAnnotationWorkspaceMediaController({
  currentDataset,
  currentEpisode,
  currentFrame,
  totalFrames,
  originalFrameIndex,
  activePlaybackRange,
  playbackRangeStart,
  playbackRangeEnd,
  isPlaying,
  playbackSpeed,
  autoPlay,
  autoLoop,
  shouldLoopPlaybackRange,
  displayAdjustment,
  displayActive,
  globalTransform,
  insertedFrames,
  removedFrames,
  onSetCurrentFrame,
  onTogglePlayback,
  onSetFrameWithinPlaybackRange,
  onRecordEvent,
}: UseAnnotationWorkspaceMediaControllerOptions) {
  const datasetFps = currentDataset?.fps ?? 30
  const mediaSources = useAnnotationWorkspaceMediaSources({
    currentDataset,
    currentEpisode,
    currentFrame,
    totalFrames,
    originalFrameIndex,
    displayAdjustment,
    displayActive,
    globalTransform,
    insertedFrames,
    removedFrames,
  })

  useFramePrefetch({
    datasetId: currentDataset?.id ?? null,
    episodeIndex: currentEpisode?.meta.index ?? null,
    cameraName: mediaSources.cameraName,
    currentFrame,
    totalFrames,
    isPlaying,
    videoSrc: mediaSources.videoSrc,
  })

  const videoSync = useAnnotationWorkspaceVideoSync({
    currentFrame,
    totalFrames,
    originalFrameIndex,
    activePlaybackRange,
    playbackRangeStart,
    playbackRangeEnd,
    isPlaying,
    playbackSpeed,
    autoPlay,
    autoLoop,
    shouldLoopPlaybackRange,
    datasetFps,
    videoStartTime: currentEpisode?.videoStartTime ?? 0,
    insertedFrames,
    removedFrames,
    videoSrc: mediaSources.videoSrc,
    onSetCurrentFrame,
    onTogglePlayback,
    onSetFrameWithinPlaybackRange,
    onRecordEvent,
  })

  return {
    ...mediaSources,
    ...videoSync,
  }
}
