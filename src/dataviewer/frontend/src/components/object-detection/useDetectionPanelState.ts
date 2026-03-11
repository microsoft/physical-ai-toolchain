import { useEffect, useMemo, useState } from 'react'

import { useObjectDetection } from '@/hooks/use-object-detection'
import { useDatasetStore, useEpisodeStore, usePlaybackControls } from '@/stores'

export function useDetectionPanelState() {
  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const currentEpisode = useEpisodeStore((state) => state.currentEpisode)
  const { currentFrame, setCurrentFrame, isPlaying, togglePlayback, playbackSpeed, setPlaybackSpeed } = usePlaybackControls()

  const {
    data,
    filteredData,
    isLoading,
    isRunning,
    error,
    needsRerun,
    filters,
    setFilters,
    runDetection,
    availableClasses,
  } = useObjectDetection()

  const [progress, setProgress] = useState(0)
  const totalFrames = currentEpisode?.meta.length || 100

  useEffect(() => {
    if (!isRunning) {
      setProgress(0)
      return
    }

    const estimatedTotalTime = totalFrames * 50
    const intervalTime = 100
    const increment = (intervalTime / estimatedTotalTime) * 100

    const interval = setInterval(() => {
      setProgress((previousValue) => {
        const nextValue = previousValue + increment
        return nextValue >= 95 ? 95 : nextValue
      })
    }, intervalTime)

    return () => clearInterval(interval)
  }, [isRunning, totalFrames])

  useEffect(() => {
    if (data && !isRunning && progress > 0) {
      setProgress(100)
      const timeout = setTimeout(() => setProgress(0), 1000)
      return () => clearTimeout(timeout)
    }
  }, [data, isRunning, progress])

  const currentDetections = useMemo(() => {
    if (!filteredData) {
      return []
    }

    const frameResult = filteredData.detections_by_frame.find((result) => result.frame === currentFrame)
    return frameResult?.detections || []
  }, [currentFrame, filteredData])

  const imageUrl = useMemo(() => {
    if (!currentDataset || !currentEpisode) {
      return null
    }

    return `/api/datasets/${currentDataset.id}/episodes/${currentEpisode.meta.index}/frames/${currentFrame}?camera=il-camera`
  }, [currentDataset, currentEpisode, currentFrame])

  return {
    availableClasses,
    currentDataset,
    currentDetections,
    currentEpisode,
    currentFrame,
    data,
    error,
    filteredData,
    filters,
    imageUrl,
    isLoading,
    isPlaying,
    isRunning,
    needsRerun,
    playbackSpeed,
    progress,
    runDetection,
    setCurrentFrame,
    setFilters,
    setPlaybackSpeed,
    togglePlayback,
    totalFrames,
  }
}
