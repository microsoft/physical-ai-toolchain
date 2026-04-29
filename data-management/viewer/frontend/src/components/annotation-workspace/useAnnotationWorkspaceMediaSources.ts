import { useEffect, useMemo, useRef, useState } from 'react'

import { combineCssFilters } from '@/lib/css-filters'
import type { DatasetInfo, EpisodeData } from '@/types'
import type { ColorAdjustment, FrameInsertion, ImageTransform } from '@/types/episode-edit'

interface UseAnnotationWorkspaceMediaSourcesOptions {
  currentDataset: DatasetInfo | null
  currentEpisode: EpisodeData | null
  currentFrame: number
  totalFrames: number
  originalFrameIndex: number | null
  displayAdjustment: ColorAdjustment | null
  displayActive: boolean
  globalTransform: ImageTransform | null
  insertedFrames: Map<number, FrameInsertion>
  removedFrames: Set<number>
}

export function useAnnotationWorkspaceMediaSources({
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
}: UseAnnotationWorkspaceMediaSourcesOptions) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [interpolatedImageUrl, setInterpolatedImageUrl] = useState<string | null>(null)

  const displayFilter = useMemo(
    () =>
      combineCssFilters(
        displayAdjustment ?? undefined,
        displayActive,
        globalTransform?.colorAdjustment,
        globalTransform?.colorFilter,
      ),
    [
      displayAdjustment,
      displayActive,
      globalTransform?.colorAdjustment,
      globalTransform?.colorFilter,
    ],
  )

  const cameras = useMemo(() => {
    const fromEpisode = currentEpisode?.cameras ?? []
    if (fromEpisode.length > 0) {
      return fromEpisode
    }
    return Object.keys(currentEpisode?.videoUrls ?? {})
  }, [currentEpisode?.cameras, currentEpisode?.videoUrls])

  const [cameraName, setCameraName] = useState<string | null>(null)

  // Reset selected camera when the camera list changes (e.g., new episode/dataset).
  // Preserves selection if the same camera is still available.
  useEffect(() => {
    if (cameras.length === 0) {
      setCameraName(null)
      return
    }
    setCameraName((prev) => (prev && cameras.includes(prev) ? prev : cameras[0]))
  }, [cameras])

  const videoSrc = useMemo(() => {
    if (!currentEpisode?.videoUrls || !cameraName) {
      return null
    }

    return currentEpisode.videoUrls[cameraName]
  }, [cameraName, currentEpisode?.videoUrls])

  const isInsertedFrame = originalFrameIndex === null

  const adjacentFrames = useMemo(() => {
    if (!isInsertedFrame) {
      return null
    }

    const originalFrameCount =
      currentEpisode?.meta.length ?? currentEpisode?.trajectoryData?.length ?? totalFrames
    const sortedInsertions = Array.from(insertedFrames.keys())
      .filter((afterIdx) => !removedFrames.has(afterIdx) && afterIdx < originalFrameCount - 1)
      .sort((a, b) => a - b)

    for (const afterIdx of sortedInsertions) {
      let insertPos = afterIdx + 1

      for (const removedIdx of removedFrames) {
        if (removedIdx <= afterIdx) {
          insertPos--
        }
      }

      for (const prevIdx of sortedInsertions) {
        if (prevIdx < afterIdx) {
          insertPos++
        }
      }

      if (insertPos === currentFrame) {
        const insertion = insertedFrames.get(afterIdx)
        return {
          beforeFrame: afterIdx,
          afterFrame: afterIdx + 1,
          factor: insertion?.interpolationFactor ?? 0.5,
        }
      }
    }

    return null
  }, [
    currentEpisode?.meta.length,
    currentEpisode?.trajectoryData?.length,
    currentFrame,
    insertedFrames,
    isInsertedFrame,
    removedFrames,
    totalFrames,
  ])

  const frameImageUrl = useMemo(() => {
    if (!currentDataset || !currentEpisode || !cameraName || originalFrameIndex === null) {
      return null
    }

    return `/api/datasets/${currentDataset.id}/episodes/${currentEpisode.meta.index}/frames/${originalFrameIndex}?camera=${encodeURIComponent(cameraName)}`
  }, [cameraName, currentDataset, currentEpisode, originalFrameIndex])

  useEffect(() => {
    if (!isInsertedFrame || !adjacentFrames || !currentDataset || !currentEpisode || !cameraName) {
      setInterpolatedImageUrl(null)
      return
    }

    const canvas = canvasRef.current
    if (!canvas) {
      return
    }

    const ctx = canvas.getContext('2d')
    if (!ctx) {
      return
    }

    const encodedCamera = encodeURIComponent(cameraName)
    const beforeUrl = `/api/datasets/${currentDataset.id}/episodes/${currentEpisode.meta.index}/frames/${adjacentFrames.beforeFrame}?camera=${encodedCamera}`
    const afterUrl = `/api/datasets/${currentDataset.id}/episodes/${currentEpisode.meta.index}/frames/${adjacentFrames.afterFrame}?camera=${encodedCamera}`

    const img1 = new Image()
    const img2 = new Image()
    let loadedCount = 0

    const blend = () => {
      loadedCount++
      if (loadedCount < 2) {
        return
      }

      canvas.width = img1.width
      canvas.height = img1.height

      ctx.globalAlpha = 1 - adjacentFrames.factor
      ctx.drawImage(img1, 0, 0)

      ctx.globalAlpha = adjacentFrames.factor
      ctx.drawImage(img2, 0, 0)

      ctx.globalAlpha = 1
      setInterpolatedImageUrl(canvas.toDataURL('image/jpeg', 0.9))
    }

    img1.onload = blend
    img2.onload = blend
    img1.src = beforeUrl
    img2.src = afterUrl

    return () => {
      img1.onload = null
      img2.onload = null
    }
  }, [adjacentFrames, cameraName, currentDataset, currentEpisode, isInsertedFrame])

  return {
    canvasRef,
    cameras,
    cameraName,
    setCameraName,
    displayFilter,
    frameImageUrl,
    interpolatedImageUrl,
    isInsertedFrame,
    videoSrc,
  }
}
